from __future__ import annotations

import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from mom_select.data import DataProviderError, EastmoneyDataProvider, PRICE_COLUMNS


LOG = logging.getLogger("mom-select.stock.data")
UNIVERSE_COLUMNS = ["code", "name", "board", "price", "turnover", "trade_status", "quote_date"]


class _RequestGate:
    """Reserve globally spaced request slots across all history worker threads."""

    def __init__(self, interval: float):
        self.interval = interval
        self._lock = threading.Lock()
        self._next_request = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            request_at = max(now, self._next_request)
            self._next_request = request_at + self.interval
        delay = request_at - now
        if delay > 0:
            time.sleep(delay)

    def cool_down(self, seconds: float) -> None:
        with self._lock:
            self._next_request = max(self._next_request, time.monotonic() + seconds)


def _is_http_403(exc: BaseException) -> bool:
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if getattr(current, "code", None) == 403 or "HTTP Error 403" in str(current):
            return True
        current = current.__cause__ or current.__context__
    return False


def _exchange_code(raw_code: str) -> str | None:
    if raw_code.startswith(("4", "8", "92")):
        return f"{raw_code}.XBSE"
    if raw_code.startswith(("5", "6", "9")):
        return f"{raw_code}.XSHG"
    if raw_code.startswith(("0", "1", "2", "3")):
        return f"{raw_code}.XSHE"
    return None


def _raw_stock_code(value: object) -> str | None:
    raw_value = str(value).strip().lower()
    match = re.fullmatch(r"(?:sh|sz|bj)?(\d{1,6})(?:\.0)?", raw_value)
    return match.group(1).zfill(6) if match else None


class AkshareStockDataProvider:
    """AKShare adapter for all-market snapshots and qfq stock histories."""

    def __init__(
        self,
        cache_dir: Path,
        *,
        offline: bool = False,
        workers: int = 8,
        universe_retries: int = 2,
    ):
        self.cache_dir = Path(cache_dir)
        self.offline = offline
        self.workers = workers
        self.universe_retries = universe_retries
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.universe_path = self.cache_dir / "universe.csv"
        self.history_dir = self.cache_dir / "history_qfq"
        self.history_dir.mkdir(parents=True, exist_ok=True)
        self._bounded_history_provider = EastmoneyDataProvider(
            self.cache_dir, workers=1, retries=1
        )
        # A full first run can contain several thousand symbols. These gates
        # apply across worker threads so public endpoints see a steady request
        # rate instead of bursts of ``workers`` concurrent requests.
        self._eastmoney_gate = _RequestGate(0.05)
        self._tencent_gate = _RequestGate(0.20)
        self._yahoo_gate = _RequestGate(0.30)
        self._source_state_lock = threading.Lock()
        self._eastmoney_consecutive_failures = 0
        self._eastmoney_disabled = False
        self.warnings: list[str] = []

    def _eastmoney_is_enabled(self) -> bool:
        with self._source_state_lock:
            return not self._eastmoney_disabled

    def _record_eastmoney_result(self, succeeded: bool) -> None:
        warning = None
        with self._source_state_lock:
            if succeeded:
                self._eastmoney_consecutive_failures = 0
                return
            self._eastmoney_consecutive_failures += 1
            if self._eastmoney_consecutive_failures >= 3 and not self._eastmoney_disabled:
                self._eastmoney_disabled = True
                warning = "东方财富个股日线连续失败，本次剩余股票跳过该源"
        if warning:
            self.warnings.append(warning)

    @staticmethod
    def _akshare():
        try:
            import akshare as ak
        except ImportError as exc:
            raise DataProviderError("未安装AKShare，无法获取个股数据") from exc
        return ak

    def _read_universe_cache(self, as_of: date) -> pd.DataFrame:
        if not self.universe_path.exists():
            raise DataProviderError("缺少个股全市场快照缓存")
        try:
            frame = pd.read_csv(self.universe_path, dtype={"code": str})
        except (OSError, ValueError, pd.errors.ParserError) as exc:
            raise DataProviderError(f"个股全市场缓存损坏: {exc}") from exc
        if not set(UNIVERSE_COLUMNS).issubset(frame.columns):
            raise DataProviderError("个股全市场缓存字段不完整")
        quote_dates = pd.to_datetime(frame["quote_date"], errors="coerce").dt.date
        if not (quote_dates == as_of).all():
            raise DataProviderError(f"个股全市场缓存不覆盖{as_of.isoformat()}")
        return frame[UNIVERSE_COLUMNS]

    @staticmethod
    def _normalize_universe(source: pd.DataFrame, source_name: str, as_of: date) -> pd.DataFrame:
        if source is None or source.empty:
            raise DataProviderError(f"{source_name}全市场A股快照为空")
        if source_name == "腾讯":
            rename = {
                "code": "raw_code",
                "name": "name",
                "zxj": "price",
                "turnover": "turnover",
                "state": "trade_status",
            }
            # Tencent returns zxj in yuan and turnover in ten-thousand yuan.
            price_scale = 1.0
            turnover_scale = 10_000.0
        else:
            rename = {
                "代码": "raw_code",
                "名称": "name",
                "最新价": "price",
                "成交额": "turnover",
            }
            price_scale = 1.0
            turnover_scale = 1.0
        normalized = source.rename(columns=rename)
        required = {"raw_code", "name", "price", "turnover"}
        if not required.issubset(normalized.columns):
            missing = ", ".join(sorted(required - set(normalized.columns)))
            raise DataProviderError(f"{source_name}全市场A股快照缺少字段: {missing}")

        rows = []
        for row in normalized.to_dict("records"):
            raw_code = _raw_stock_code(row["raw_code"])
            if raw_code is None:
                continue
            code = _exchange_code(raw_code)
            if code is None:
                continue
            name = str(row.get("name") or raw_code).strip()
            price = pd.to_numeric(row.get("price"), errors="coerce")
            turnover = pd.to_numeric(row.get("turnover"), errors="coerce")
            board = (
                "创业板" if raw_code.startswith(("300", "301"))
                else "科创板" if raw_code.startswith(("688", "689"))
                else "北交所" if code.endswith(".XBSE")
                else "沪深主板"
            )
            rows.append(
                {
                    "code": code,
                    "name": name,
                    "board": board,
                    "price": float(price) * price_scale if pd.notna(price) else float("nan"),
                    "turnover": (
                        float(turnover) * turnover_scale
                        if pd.notna(turnover)
                        else float("nan")
                    ),
                    "trade_status": str(row.get("trade_status") or "正常").strip(),
                    "quote_date": as_of.isoformat(),
                }
            )
        frame = pd.DataFrame(rows, columns=UNIVERSE_COLUMNS).drop_duplicates("code")
        if frame.empty:
            raise DataProviderError(f"{source_name}全市场A股快照没有有效证券")
        return frame.reset_index(drop=True)

    def _fetch_universe(self, as_of: date) -> tuple[pd.DataFrame, str, list[str]]:
        ak = self._akshare()
        sources = (
            ("腾讯", ak.stock_zh_a_spot_tx, self.universe_retries),
            ("东方财富", ak.stock_zh_a_spot_em, self.universe_retries),
            # Sina warns that repeated calls may temporarily block the caller.
            ("新浪", ak.stock_zh_a_spot, 1),
        )
        errors: list[str] = []
        for source_name, fetch, retries in sources:
            for attempt in range(retries):
                try:
                    frame = self._normalize_universe(fetch(), source_name, as_of)
                    return frame, source_name, errors
                except Exception as exc:
                    errors.append(f"{source_name}第{attempt + 1}次: {exc}")
                    LOG.warning(
                        "全市场A股快照源失败: source=%s attempt=%d/%d",
                        source_name,
                        attempt + 1,
                        retries,
                        exc_info=True,
                    )
                    if attempt + 1 < retries:
                        time.sleep(0.5 * (2**attempt))
        raise DataProviderError("所有全市场A股快照源均失败: " + "; ".join(errors))

    def stock_universe(self, as_of: date) -> pd.DataFrame:
        today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
        if self.offline:
            return self._read_universe_cache(as_of)
        if as_of < today:
            try:
                return self._read_universe_cache(as_of)
            except DataProviderError as exc:
                raise DataProviderError(
                    f"历史日期{as_of.isoformat()}缺少同日全市场快照；"
                    "公开接口只能补个股和基准历史行情，不能还原历史全市场快照"
                ) from exc
        if as_of > today:
            raise DataProviderError("全市场快照日期不能晚于当前日期")
        try:
            frame, source_name, failed_sources = self._fetch_universe(as_of)
            frame.to_csv(self.universe_path, index=False)
            if failed_sources:
                self.warnings.append(
                    f"全市场A股快照已回退到{source_name}；"
                    + "；".join(failed_sources)
                )
            LOG.info("全市场A股快照获取完成: source=%s count=%d", source_name, len(frame))
            return frame
        except Exception as exc:
            try:
                cached = self._read_universe_cache(as_of)
            except Exception as cache_exc:
                raise DataProviderError(
                    f"全市场A股快照获取失败，且无可用当日缓存: {exc}; 缓存: {cache_exc}"
                ) from exc
            if cached.empty:
                raise DataProviderError("个股全市场快照缓存为空") from exc
            self.warnings.append(f"全市场A股快照更新失败，使用当日本地缓存: {exc}")
            LOG.warning("全市场A股快照更新失败，使用当日本地缓存", exc_info=True)
            return cached

    def _history_path(self, code: str) -> Path:
        return self.history_dir / f"{code}.csv"

    @staticmethod
    def _normalize_history(frame: pd.DataFrame, source_name: str) -> pd.DataFrame:
        if frame is None or frame.empty:
            return pd.DataFrame(columns=PRICE_COLUMNS)
        if source_name == "腾讯":
            frame = frame.rename(columns={"amount": "turnover_value"})
            if "turnover_value" not in frame.columns:
                raise DataProviderError("腾讯前复权日线缺少成交额字段amount")
            frame = frame.drop(columns=["turnover"], errors="ignore").rename(
                columns={"turnover_value": "turnover"}
            )
        else:
            frame = frame.rename(
                columns={
                    "日期": "date",
                    "开盘": "open",
                    "收盘": "close",
                    "最高": "high",
                    "最低": "low",
                    "成交量": "volume",
                    "成交额": "turnover",
                }
            )
        if not set(PRICE_COLUMNS).issubset(frame.columns):
            missing = ", ".join(sorted(set(PRICE_COLUMNS) - set(frame.columns)))
            raise DataProviderError(f"{source_name}前复权日线缺少字段: {missing}")
        frame = frame.copy()
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
        for column in PRICE_COLUMNS[1:]:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
        return frame[PRICE_COLUMNS].dropna().sort_values("date").drop_duplicates("date")

    def _read_history(self, code: str, start: date, end: date) -> pd.DataFrame:
        path = self._history_path(code)
        if not path.exists():
            return pd.DataFrame(columns=PRICE_COLUMNS)
        try:
            frame = pd.read_csv(path, parse_dates=["date"])
        except (OSError, ValueError, pd.errors.ParserError) as exc:
            self.warnings.append(f"{code}前复权日线缓存损坏，按无缓存处理: {exc}")
            return pd.DataFrame(columns=PRICE_COLUMNS)
        if not set(PRICE_COLUMNS).issubset(frame.columns):
            self.warnings.append(f"{code}前复权日线缓存字段不完整，按无缓存处理")
            return pd.DataFrame(columns=PRICE_COLUMNS)
        return frame.loc[
            (frame["date"].dt.date >= start) & (frame["date"].dt.date <= end),
            PRICE_COLUMNS,
        ].sort_values("date")

    def history(self, code: str, start: date, end: date) -> pd.DataFrame:
        cached = self._read_history(code, start, end)
        if self.offline:
            return cached
        if not cached.empty and cached["date"].max().date() >= end:
            return cached
        raw_code = code.split(".", 1)[0]
        fetch_start = start
        if not cached.empty and len(cached) >= 120:
            fetch_start = cached["date"].max().date() + timedelta(days=1)
        errors: list[str] = []
        frame = pd.DataFrame(columns=PRICE_COLUMNS)
        if self._eastmoney_is_enabled():
            try:
                self._eastmoney_gate.wait()
                source = self._akshare().stock_zh_a_hist(
                    symbol=raw_code,
                    period="daily",
                    start_date=fetch_start.strftime("%Y%m%d"),
                    end_date=end.strftime("%Y%m%d"),
                    adjust="qfq",
                    timeout=15,
                )
                frame = self._normalize_history(source, "东方财富")
                self._record_eastmoney_result(not frame.empty)
            except Exception as exc:
                self._record_eastmoney_result(False)
                errors.append(f"东方财富: {exc}")
        else:
            errors.append("东方财富: 本次运行已熔断")
        if frame.empty and not code.endswith(".XBSE"):
            try:
                self._tencent_gate.wait()
                frame = self._bounded_history_provider.tencent_qfq_history(
                    code, fetch_start, end
                )
                if errors:
                    self.warnings.append(f"{code}前复权日线已回退到腾讯")
            except Exception as exc:
                if _is_http_403(exc):
                    self._tencent_gate.cool_down(30.0)
                errors.append(f"腾讯: {exc}")
        if frame.empty and code.endswith((".XSHG", ".XSHE")):
            try:
                self._yahoo_gate.wait()
                frame = self._bounded_history_provider.yahoo_qfq_history(
                    code, fetch_start, end
                )
                if errors:
                    self.warnings.append(f"{code}前复权日线已回退到Yahoo")
            except Exception as exc:
                errors.append(f"Yahoo: {exc}")
        if frame.empty and errors:
            detail = "; ".join(errors)
            if not cached.empty:
                self.warnings.append(f"{code}前复权日线更新失败，使用缓存: {detail}")
                return cached
            raise DataProviderError(f"{code}前复权日线获取失败: {detail}")
        if frame is None or frame.empty:
            if not cached.empty:
                return cached
            raise DataProviderError(f"{code}前复权日线为空")
        if not cached.empty:
            frame = (
                pd.concat([cached, frame], ignore_index=True)
                .sort_values("date")
                .drop_duplicates("date", keep="last")
            )
        frame.to_csv(self._history_path(code), index=False)
        return frame

    def histories(
        self, codes: list[str], start: date, end: date
    ) -> tuple[dict[str, pd.DataFrame], dict[str, str]]:
        histories: dict[str, pd.DataFrame] = {}
        failures: dict[str, str] = {}

        def load(code: str) -> pd.DataFrame:
            return self.history(code, start, end)

        with ThreadPoolExecutor(max_workers=self.workers) as executor:
            futures = {executor.submit(load, code): code for code in codes}
            for future in as_completed(futures):
                code = futures[future]
                try:
                    frame = future.result()
                    if frame.empty:
                        failures[code] = "没有有效前复权日线"
                    else:
                        histories[code] = frame
                except Exception as exc:
                    LOG.error("个股行情获取失败: code=%s", code, exc_info=exc)
                    failures[code] = str(exc)
        for diagnostic in sorted(self._bounded_history_provider.warnings):
            if diagnostic not in self.warnings:
                self.warnings.append(diagnostic)
        return histories, failures
