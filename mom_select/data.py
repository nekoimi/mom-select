from __future__ import annotations

import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Protocol
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from calendar import timegm
from zoneinfo import ZoneInfo

import pandas as pd


SHANGHAI = ZoneInfo("Asia/Shanghai")
LOG = logging.getLogger("mom-select.data")


PRICE_COLUMNS = ["date", "open", "close", "high", "low", "volume", "turnover"]
# The momentum calculation needs 25 prior sessions plus the signal session.
# Keep this local to the data adapter so a long calendar-day request does not
# force a network refresh when the cache already contains enough trading days.
MIN_HISTORY_ROWS = 26


@dataclass(frozen=True)
class SnapshotBatch:
    frames: dict[str, pd.DataFrame]
    quote_times: dict[str, datetime]


class DataProvider(Protocol):
    def history(
        self, code: str, start: date, end: date, full_window: bool = False
    ) -> pd.DataFrame: ...

    def histories(
        self, codes: list[str], start: date, end: date, full_window: bool = False
    ) -> tuple[dict[str, pd.DataFrame], dict[str, str]]: ...

    def security_name(self, code: str) -> str: ...

    def supplement_close_snapshots(
        self, codes: list[str], trading_date: date
    ) -> dict[str, pd.DataFrame]: ...

    def realtime_snapshots(
        self, codes: list[str], trading_date: date
    ) -> SnapshotBatch: ...

    def historical_intraday_snapshots(
        self, codes: list[str], trading_date: date, cutoff: datetime
    ) -> SnapshotBatch: ...

    def cached_snapshots(
        self, codes: list[str], trading_date: date
    ) -> dict[str, pd.DataFrame]: ...

    def etf_universe(self) -> pd.DataFrame: ...

    def liquidity_histories(
        self, codes: list[str], start: date, end: date
    ) -> tuple[dict[str, pd.DataFrame], dict[str, str]]: ...


class DataProviderError(RuntimeError):
    pass


def _empty_frame() -> pd.DataFrame:
    frame = pd.DataFrame(columns=PRICE_COLUMNS)
    frame["date"] = pd.Series(dtype="datetime64[ns]")
    return frame


class EastmoneyDataProvider:
    """Public market-data adapter with per-symbol CSV caching.

    Eastmoney is the primary endpoint. Tencent daily bars are used as a
    fallback; because they do not include turnover, it is estimated from the
    average OHLC price and volume. These unofficial endpoints are intended for
    research/advisory use only.
    """

    endpoint = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
    fallback_endpoint = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
    yahoo_endpoint = "https://query1.finance.yahoo.com/v8/finance/chart"
    snapshot_endpoint = "https://push2.eastmoney.com/api/qt/stock/get"
    batch_snapshot_endpoint = "https://push2.eastmoney.com/api/qt/ulist.np/get"
    tencent_snapshot_endpoint = "https://qt.gtimg.cn/q="
    etf_list_endpoint = "https://push2.eastmoney.com/api/qt/clist/get"
    fund_list_endpoint = "https://fund.eastmoney.com/js/fundcode_search.js"

    def __init__(
        self,
        cache_dir: Path,
        offline: bool = False,
        workers: int = 8,
        retries: int = 3,
    ):
        self.cache_dir = cache_dir
        self.offline = offline
        self.workers = workers
        self.retries = retries
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._metadata_path = self.cache_dir / "_metadata.json"
        self._etf_universe_path = self.cache_dir / "_etf_universe.csv"
        self._names: dict[str, str] = {}
        self.warnings: set[str] = set()
        self._load_metadata()

    def _load_metadata(self) -> None:
        if not self._metadata_path.exists():
            return
        try:
            payload = json.loads(self._metadata_path.read_text(encoding="utf-8"))
            self._names.update(payload.get("names", {}))
        except (OSError, json.JSONDecodeError):
            return

    def _save_metadata(self) -> None:
        # Warnings describe one run and must not leak into later reports.
        payload = {"names": self._names}
        self._metadata_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    @staticmethod
    def _split_code(code: str) -> tuple[str, str]:
        match = re.fullmatch(r"(\d{6})\.(XSHG|XSHE)", str(code))
        if not match:
            raise ValueError(f"无效证券代码: {code!r}，应为6位数字.XSHG或.XSHE")
        return match.group(1), match.group(2)

    @classmethod
    def _secid(cls, code: str) -> str:
        raw_code, exchange = cls._split_code(code)
        market = "1" if exchange == "XSHG" else "0"
        return f"{market}.{raw_code}"

    def _cache_path(self, code: str) -> Path:
        return self.cache_dir / f"{code}.csv"

    @staticmethod
    def _symbol(code: str) -> str:
        raw_code, exchange = EastmoneyDataProvider._split_code(code)
        return f"{'sh' if exchange == 'XSHG' else 'sz'}{raw_code}"

    def _read_cache(self, code: str) -> pd.DataFrame:
        path = self._cache_path(code)
        if not path.exists():
            return _empty_frame()
        try:
            frame = pd.read_csv(path, parse_dates=["date"])
        except (OSError, ValueError, pd.errors.ParserError) as exc:
            self.warnings.add(f"ETF {code} 本地缓存损坏，将尝试重新获取: {exc}")
            return _empty_frame()
        if not set(PRICE_COLUMNS).issubset(frame.columns):
            self.warnings.add(f"ETF {code} 本地缓存缺少行情字段，将尝试重新获取")
            return _empty_frame()
        return frame[PRICE_COLUMNS].sort_values("date").drop_duplicates("date")

    def _request_json(self, request: Request) -> dict:
        last_error: Exception | None = None
        for attempt in range(self.retries):
            try:
                with urlopen(request, timeout=15) as response:
                    return json.loads(response.read().decode("utf-8"))
            except Exception as exc:
                last_error = exc
                if attempt + 1 < self.retries:
                    time.sleep(0.4 * (2**attempt))
        raise DataProviderError(f"行情请求失败（重试{self.retries}次）: {last_error}") from last_error

    def _read_etf_universe_cache(self) -> pd.DataFrame:
        if not self._etf_universe_path.exists():
            return pd.DataFrame(columns=["code", "name"])
        frame = pd.read_csv(self._etf_universe_path, dtype=str)
        if not {"code", "name"}.issubset(frame.columns):
            return pd.DataFrame(columns=["code", "name"])
        return frame[["code", "name"]].drop_duplicates("code").reset_index(drop=True)

    def _fetch_fund_etf_universe(self) -> pd.DataFrame:
        request = Request(
            self.fund_list_endpoint,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://fund.eastmoney.com/",
            },
        )
        last_error: Exception | None = None
        for attempt in range(self.retries):
            try:
                with urlopen(request, timeout=30) as response:
                    text = response.read().decode("utf-8-sig")
                payload = text.split("=", 1)[1].strip().rstrip(";")
                items = json.loads(payload)
                rows = []
                for item in items:
                    if len(item) < 4:
                        continue
                    raw_code = str(item[0]).strip()
                    name = str(item[2]).strip()
                    if (
                        "ETF" not in name.upper()
                        or "联接" in name
                        or not raw_code.startswith(("159", "51", "52", "56", "58"))
                    ):
                        continue
                    exchange = "XSHE" if raw_code.startswith("159") else "XSHG"
                    rows.append({"code": f"{raw_code}.{exchange}", "name": name})
                return pd.DataFrame(rows, columns=["code", "name"]).drop_duplicates("code")
            except Exception as exc:
                last_error = exc
                if attempt + 1 < self.retries:
                    time.sleep(0.4 * (2**attempt))
        raise DataProviderError(f"备用ETF清单获取失败: {last_error}") from last_error

    def etf_universe(self) -> pd.DataFrame:
        cached = self._read_etf_universe_cache()
        if self.offline:
            if cached.empty:
                raise DataProviderError("离线模式缺少全市场ETF清单缓存")
            return cached

        rows: list[dict[str, str]] = []
        page = 1
        page_size = 100
        try:
            while True:
                query = urlencode(
                    {
                        "pn": page,
                        "pz": page_size,
                        "po": 1,
                        "np": 1,
                        "fltt": 2,
                        "invt": 2,
                        "fid": "f3",
                        "fs": "b:MK0021",
                        "fields": "f12,f13,f14",
                    }
                )
                request = Request(
                    f"{self.etf_list_endpoint}?{query}",
                    headers={
                        "User-Agent": "Mozilla/5.0",
                        "Referer": "https://quote.eastmoney.com/",
                    },
                )
                payload = self._request_json(request)
                data = payload.get("data") or {}
                items = data.get("diff") or []
                if isinstance(items, dict):
                    items = list(items.values())
                if not items:
                    break
                for item in items:
                    raw_code = str(item.get("f12") or "").strip()
                    market_value = item.get("f13")
                    market = "" if market_value is None else str(market_value).strip()
                    name = str(item.get("f14") or raw_code).strip()
                    if len(raw_code) != 6 or market not in {"0", "1"}:
                        continue
                    exchange = "XSHG" if market == "1" else "XSHE"
                    code = f"{raw_code}.{exchange}"
                    rows.append({"code": code, "name": name})
                total = int(data.get("total") or 0)
                if page * page_size >= total or len(items) < page_size:
                    break
                page += 1
        except Exception as exc:
            try:
                fallback = self._fetch_fund_etf_universe()
            except Exception as fallback_exc:
                if cached.empty:
                    raise DataProviderError(
                        f"获取全市场ETF清单失败: {exc}; 备用清单失败: {fallback_exc}"
                    ) from fallback_exc
                self.warnings.add(f"全市场ETF清单更新失败，使用本地缓存：{exc}")
                return cached
            if fallback.empty:
                if cached.empty:
                    raise DataProviderError("备用ETF清单未返回有效场内ETF") from exc
                self.warnings.add("备用ETF清单为空，使用本地缓存")
                return cached
            self.warnings.add("东方财富ETF板块清单不可用，本次使用基金清单筛选场内ETF")
            rows = fallback.to_dict("records")

        frame = pd.DataFrame(rows, columns=["code", "name"]).drop_duplicates("code")
        if frame.empty:
            if cached.empty:
                raise DataProviderError("全市场ETF接口未返回有效清单")
            self.warnings.add("全市场ETF接口返回空清单，使用本地缓存")
            return cached
        frame = frame.sort_values("code").reset_index(drop=True)
        frame.to_csv(self._etf_universe_path, index=False)
        self._names.update(dict(zip(frame["code"], frame["name"], strict=True)))
        self._save_metadata()
        return frame

    def _fetch_eastmoney(self, code: str, start: date, end: date) -> pd.DataFrame:
        query = urlencode(
            {
                "secid": self._secid(code),
                "fields1": "f1,f2,f3,f4,f5,f6",
                "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
                "klt": "101",
                "fqt": "1",
                "beg": start.strftime("%Y%m%d"),
                "end": end.strftime("%Y%m%d"),
                "lmt": "1000000",
            }
        )
        request = Request(
            f"{self.endpoint}?{query}",
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"},
        )
        payload = self._request_json(request)
        data = payload.get("data")
        if not data or not data.get("klines"):
            raise DataProviderError("行情接口未返回K线")
        self._names[code] = data.get("name") or code
        rows = []
        for item in data["klines"]:
            values = item.split(",")
            rows.append(
                {
                    "date": values[0],
                    "open": values[1],
                    "close": values[2],
                    "high": values[3],
                    "low": values[4],
                    "volume": values[5],
                    "turnover": values[6],
                }
            )
        frame = pd.DataFrame(rows)
        frame["date"] = pd.to_datetime(frame["date"])
        for column in PRICE_COLUMNS[1:]:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
        return frame[PRICE_COLUMNS].dropna().sort_values("date")

    def _fetch_tencent(self, code: str, start: date, end: date) -> pd.DataFrame:
        symbol = self._symbol(code)
        # Tencent's endpoint rejects the comma-delimited param when commas are
        # percent-encoded by urlencode; keep this query component literal.
        query = (
            f"param={symbol},day,{start.isoformat()},{end.isoformat()},1000,qfq"
        )
        request = Request(
            f"{self.fallback_endpoint}?{query}",
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"},
        )
        payload = self._request_json(request)
        security_data = payload.get("data", {}).get(symbol, {})
        raw_rows = security_data.get("qfqday") or security_data.get("day")
        if not raw_rows:
            raise DataProviderError("备用行情接口未返回K线")
        quote = (security_data.get("qt") or {}).get(symbol) or []
        if len(quote) > 1 and quote[1]:
            self._names[code] = str(quote[1])
        rows = []
        for values in raw_rows:
            open_price = float(values[1])
            close = float(values[2])
            high = float(values[3])
            low = float(values[4])
            volume_lots = float(values[5])
            volume = volume_lots * 100
            typical_price = (open_price + close + high + low) / 4
            rows.append(
                {
                    "date": values[0],
                    "open": open_price,
                    "close": close,
                    "high": high,
                    "low": low,
                    "volume": volume,
                    "turnover": typical_price * volume,
                }
            )
        frame = pd.DataFrame(rows)
        frame["date"] = pd.to_datetime(frame["date"])
        self.warnings.add("部分行情使用腾讯备用日线，成交额为OHLC均价乘成交量的估算值")
        return frame[PRICE_COLUMNS].dropna().sort_values("date")

    def _fetch_yahoo(self, code: str, start: date, end: date) -> pd.DataFrame:
        """Fetch daily bars from Yahoo Finance as a last-resort public source."""
        raw_code, exchange = self._split_code(code)
        suffix = ".SZ" if exchange == "XSHE" else ".SS"
        period1 = timegm(start.timetuple())
        # Yahoo's period2 is exclusive; include the requested end date.
        period2 = timegm((end + timedelta(days=1)).timetuple())
        query = urlencode(
            {
                "period1": period1,
                "period2": period2,
                "interval": "1d",
                "events": "history",
                "includeAdjustedClose": "true",
            }
        )
        request = Request(
            f"{self.yahoo_endpoint}/{raw_code}{suffix}?{query}",
            headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
        )
        payload = self._request_json(request)
        result = ((payload.get("chart") or {}).get("result") or [None])[0]
        if not result:
            raise DataProviderError("Yahoo行情接口未返回K线")
        timestamps = result.get("timestamp") or []
        quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
        rows = []
        for index, timestamp in enumerate(timestamps):
            try:
                open_price = quote["open"][index]
                close = quote["close"][index]
                high = quote["high"][index]
                low = quote["low"][index]
                volume = quote["volume"][index]
                if any(value is None for value in (open_price, close, high, low, volume)):
                    continue
                typical_price = (float(open_price) + float(close) + float(high) + float(low)) / 4
                rows.append(
                    {
                        "date": pd.to_datetime(int(timestamp), unit="s").normalize(),
                        "open": float(open_price),
                        "close": float(close),
                        "high": float(high),
                        "low": float(low),
                        "volume": float(volume),
                        "turnover": typical_price * float(volume),
                    }
                )
            except (KeyError, IndexError, TypeError, ValueError):
                continue
        if not rows:
            raise DataProviderError("Yahoo行情接口未返回有效K线")
        self.warnings.add("部分行情使用Yahoo备用日线，成交额为OHLC均价乘成交量的估算值")
        return pd.DataFrame(rows, columns=PRICE_COLUMNS).sort_values("date")

    def _fetch_akshare(self, code: str, start: date, end: date) -> pd.DataFrame:
        """Fetch adjusted ETF daily bars through the optional AKShare adapter."""
        try:
            import akshare as ak
        except ImportError as exc:
            raise DataProviderError("未安装AKShare") from exc
        raw_code, exchange = self._split_code(code)
        errors: list[str] = []
        try:
            frame = ak.fund_etf_hist_em(
                symbol=raw_code,
                period="daily",
                start_date=start.strftime("%Y%m%d"),
                end_date=end.strftime("%Y%m%d"),
                adjust="qfq",
            )
        except Exception as exc:
            errors.append(f"ETF日线: {exc}")
            frame = pd.DataFrame()
        if frame is None or frame.empty:
            try:
                symbol = f"{'sh' if exchange == 'XSHG' else 'sz'}{raw_code}"
                frame = ak.stock_zh_index_daily_em(
                    symbol=symbol,
                    start_date=start.strftime("%Y%m%d"),
                    end_date=end.strftime("%Y%m%d"),
                )
            except Exception as exc:
                errors.append(f"指数日线: {exc}")
                frame = pd.DataFrame()
        if frame is None or frame.empty:
            detail = "; ".join(errors) or "接口返回空数据"
            raise DataProviderError(f"AKShare未返回日线: {detail}")
        columns = {
            "日期": "date",
            "开盘": "open",
            "收盘": "close",
            "最高": "high",
            "最低": "low",
            "成交量": "volume",
            "成交额": "turnover",
            "amount": "turnover",
        }
        frame = frame.rename(columns=columns)
        if not set(PRICE_COLUMNS).issubset(frame.columns):
            raise DataProviderError("AKShare ETF日线字段不完整")
        for column in PRICE_COLUMNS[1:]:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
        result = frame[PRICE_COLUMNS].dropna().sort_values("date")
        if result.empty:
            raise DataProviderError("AKShare未返回有效ETF日线")
        self.warnings.add("部分行情使用AKShare ETF日线")
        return result

    def _fetch_yahoo_intraday_snapshot(
        self, code: str, trading_date: date, cutoff: datetime
    ) -> tuple[pd.DataFrame, datetime]:
        raw_code, exchange = self._split_code(code)
        suffix = ".SZ" if exchange == "XSHE" else ".SS"
        query = urlencode(
            {
                "period1": timegm(trading_date.timetuple()),
                "period2": timegm((trading_date + timedelta(days=1)).timetuple()),
                "interval": "1m",
                "events": "history",
            }
        )
        request = Request(
            f"{self.yahoo_endpoint}/{raw_code}{suffix}?{query}",
            headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
        )
        # Historical minute bars are an optional enhancement. Use one short
        # request here so an unavailable symbol does not consume the normal
        # three-retry daily-data budget.
        try:
            with urlopen(request, timeout=2) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            raise DataProviderError(f"Yahoo分钟行情请求失败: {exc}") from exc
        result = ((payload.get("chart") or {}).get("result") or [None])[0]
        if not result:
            raise DataProviderError("Yahoo分钟行情接口未返回数据")
        timestamps = result.get("timestamp") or []
        quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
        day_bars = []
        for index, timestamp in enumerate(timestamps):
            quote_time = datetime.fromtimestamp(int(timestamp), tz=SHANGHAI)
            if quote_time.date() != trading_date:
                continue
            try:
                values = (
                    quote["open"][index],
                    quote["close"][index],
                    quote["high"][index],
                    quote["low"][index],
                    quote["volume"][index],
                )
                if any(value is None for value in values):
                    continue
                day_bars.append((quote_time, *(float(value) for value in values)))
            except (KeyError, IndexError, TypeError, ValueError):
                continue
        bars = [
            item for item in day_bars if item[0].replace(tzinfo=None) <= cutoff
        ]
        if not bars:
            raise DataProviderError("Yahoo分钟行情在截止时间前无有效数据")
        volume_scale = 1.0
        cached_day = self._read_cache(code)
        cached_day = cached_day.loc[cached_day["date"].dt.date == trading_date]
        raw_full_day_volume = sum(item[5] for item in day_bars)
        if not cached_day.empty and raw_full_day_volume > 0:
            official_volume = float(cached_day["volume"].iloc[-1])
            if official_volume > 0:
                volume_scale = official_volume / raw_full_day_volume
        quote_time = bars[-1][0].replace(tzinfo=None)
        open_price = bars[0][1]
        close = bars[-1][2]
        high = max(item[3] for item in bars)
        low = min(item[4] for item in bars)
        volume = sum(item[5] for item in bars) * volume_scale
        turnover = sum(
            ((item[1] + item[2] + item[3] + item[4]) / 4) * item[5]
            for item in bars
        ) * volume_scale
        frame = pd.DataFrame(
            [
                {
                    "date": pd.Timestamp(trading_date),
                    "open": open_price,
                    "close": close,
                    "high": high,
                    "low": low,
                    "volume": volume,
                    "turnover": turnover,
                }
            ]
        )
        return frame, quote_time

    def historical_intraday_snapshots(
        self, codes: list[str], trading_date: date, cutoff: datetime
    ) -> SnapshotBatch:
        if self.offline or not codes:
            return SnapshotBatch({}, {})
        frames: dict[str, pd.DataFrame] = {}
        quote_times: dict[str, datetime] = {}

        def load(code: str) -> tuple[pd.DataFrame, datetime]:
            return self._fetch_yahoo_intraday_snapshot(code, trading_date, cutoff)

        with ThreadPoolExecutor(max_workers=max(self.workers, 32)) as executor:
            futures = {executor.submit(load, code): code for code in codes}
            for future in as_completed(futures):
                code = futures[future]
                try:
                    frames[code], quote_times[code] = future.result()
                except Exception:
                    continue
        if frames:
            self.warnings.add("历史DEBUG部分行情使用Yahoo 1分钟数据构建13:05快照")
        return SnapshotBatch(frames, quote_times)

    def cached_snapshots(
        self, codes: list[str], trading_date: date
    ) -> dict[str, pd.DataFrame]:
        snapshots: dict[str, pd.DataFrame] = {}
        for code in codes:
            frame = self._read_cache(code)
            if frame.empty:
                continue
            day = frame.loc[frame["date"].dt.date == trading_date]
            if not day.empty:
                snapshots[code] = day.tail(1).reset_index(drop=True)
        return snapshots

    def _fetch_close_snapshot(self, code: str, trading_date: date) -> pd.DataFrame:
        query = urlencode(
            {
                "secid": self._secid(code),
                "fields": "f43,f44,f45,f46,f47,f48,f57,f58,f59",
            }
        )
        request = Request(
            f"{self.snapshot_endpoint}?{query}",
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"},
        )
        payload = self._request_json(request)
        data = payload.get("data")
        if not data:
            raise DataProviderError("收盘快照接口未返回数据")
        precision = int(data.get("f59") or 2)
        divisor = 10**precision
        fields = [data.get(key) for key in ("f46", "f43", "f44", "f45", "f47", "f48")]
        if any(value in (None, "-") for value in fields):
            raise DataProviderError("收盘快照字段不完整")
        self._names[code] = data.get("f58") or code
        self.warnings.add("部分ETF的最新日K由收盘行情快照补齐")
        return pd.DataFrame(
            [
                {
                    "date": pd.Timestamp(trading_date),
                    "open": float(data["f46"]) / divisor,
                    "close": float(data["f43"]) / divisor,
                    "high": float(data["f44"]) / divisor,
                    "low": float(data["f45"]) / divisor,
                    "volume": float(data["f47"]) * 100,
                    "turnover": float(data["f48"]),
                }
            ]
        )

    def realtime_snapshots(
        self, codes: list[str], trading_date: date
    ) -> SnapshotBatch:
        if self.offline or not codes:
            return SnapshotBatch({}, {})
        snapshots: dict[str, pd.DataFrame] = {}
        quote_times: dict[str, datetime] = {}
        for offset in range(0, len(codes), 50):
            batch = codes[offset : offset + 50]
            symbols = ",".join(self._symbol(code) for code in batch)
            request = Request(
                f"{self.tencent_snapshot_endpoint}{symbols}",
                headers={"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"},
            )
            response_text = None
            last_error: Exception | None = None
            for attempt in range(self.retries):
                try:
                    with urlopen(request, timeout=15) as response:
                        response_text = response.read().decode("gb18030")
                    break
                except Exception as exc:
                    last_error = exc
                    if attempt + 1 < self.retries:
                        time.sleep(0.4 * (2**attempt))
            if response_text is None:
                self.warnings.add(f"收盘快照请求失败: {last_error}")
                continue
            requested = {self._symbol(code): code for code in batch}
            for symbol, content in re.findall(r'v_([^=]+)="([^"]*)";', response_text):
                code = requested.get(symbol)
                values = content.split("~")
                if code is None or len(values) < 38:
                    continue
                try:
                    quote_time = datetime.strptime(values[30][:14], "%Y%m%d%H%M%S")
                    close = float(values[3])
                    open_price = float(values[5])
                    high = float(values[33])
                    low = float(values[34])
                    volume = float(values[36]) * 100
                    turnover = float(values[35].split("/")[2])
                except (ValueError, IndexError):
                    continue
                if quote_time.date() != trading_date or volume <= 0:
                    continue
                self._names[code] = values[1] or code
                quote_times[code] = quote_time
                snapshots[code] = pd.DataFrame(
                    [
                        {
                            "date": pd.Timestamp(trading_date),
                            "open": open_price,
                            "close": close,
                            "high": high,
                            "low": low,
                            "volume": volume,
                            "turnover": turnover,
                        }
                    ]
                )
        if snapshots:
            self._save_metadata()
        return SnapshotBatch(snapshots, quote_times)

    def supplement_close_snapshots(
        self, codes: list[str], trading_date: date
    ) -> dict[str, pd.DataFrame]:
        batch = self.realtime_snapshots(codes, trading_date)
        if batch.frames:
            self.warnings.add("部分ETF的最新日K由腾讯批量收盘行情快照补齐")
            self._save_metadata()
        return batch.frames

    def merge_snapshot(self, code: str, snapshot: pd.DataFrame) -> pd.DataFrame:
        cached = self._read_cache(code)
        combined = pd.concat([cached, snapshot], ignore_index=True)
        combined["date"] = pd.to_datetime(combined["date"])
        combined = combined.sort_values("date").drop_duplicates("date", keep="last")
        combined.to_csv(self._cache_path(code), index=False)
        return combined.reset_index(drop=True)

    def _fetch(self, code: str, start: date, end: date) -> pd.DataFrame:
        try:
            return self._fetch_eastmoney(code, start, end)
        except DataProviderError as primary_error:
            try:
                return self._fetch_tencent(code, start, end)
            except DataProviderError as fallback_error:
                try:
                    return self._fetch_yahoo(code, start, end)
                except DataProviderError as yahoo_error:
                    try:
                        return self._fetch_akshare(code, start, end)
                    except DataProviderError as akshare_error:
                        raise DataProviderError(
                            f"主数据源失败: {primary_error}; 腾讯失败: {fallback_error}; "
                            f"Yahoo失败: {yahoo_error}; AKShare失败: {akshare_error}"
                        ) from akshare_error

    def history(
        self, code: str, start: date, end: date, full_window: bool = False
    ) -> pd.DataFrame:
        cached = self._read_cache(code)
        if self.offline:
            if cached.empty:
                raise DataProviderError("离线缓存不存在")
            combined = cached
        else:
            fetch_start = start
            if not cached.empty:
                cached_last = cached["date"].max().date()
                cached_first = cached["date"].min().date()
                cached_window = cached.loc[
                    (cached["date"].dt.date >= start)
                    & (cached["date"].dt.date <= end)
                ]
                # A strategy request is expressed in calendar days, while
                # calculations consume trading sessions. If the cache has
                # enough sessions and reaches ``end``, a missing earlier
                # calendar date is harmless and should not trigger a network
                # request (particularly important for historical debug runs).
                cache_covers_start = cached_first <= start + timedelta(days=7)
                has_required_cache = (
                    cache_covers_start if full_window else len(cached_window) >= MIN_HISTORY_ROWS
                )
                if cached_last >= end and has_required_cache:
                    if cached_first > start:
                        self.warnings.add(
                            "部分本地缓存未覆盖请求起始日，但已有足够交易日，继续使用缓存"
                        )
                    return cached_window.reset_index(drop=True)
                # The cache may only contain the short liquidity window. When
                # the requested history is longer, fetch from the actual
                # strategy start so the 25-session momentum window can be
                # rebuilt instead of repeatedly refreshing the last few days.
                fetch_start = start
            try:
                fetched = self._fetch(code, fetch_start, end)
            except DataProviderError as fetch_error:
                # A stale-but-current cache is still useful to the caller: it
                # can be classified as insufficient history (and excluded)
                # instead of aborting the whole pool when public endpoints are
                # temporarily unavailable. If the cache does not reach ``end``
                # we keep the original failure so stale data cannot be used.
                if not cached.empty and cached_last >= end:
                    self.warnings.add("部分ETF网络补数失败，使用现有缓存并按历史长度筛选")
                    return cached_window.reset_index(drop=True)
                raise fetch_error
            combined = pd.concat([cached, fetched], ignore_index=True)
            combined["date"] = pd.to_datetime(combined["date"])
            combined = combined.sort_values("date").drop_duplicates("date", keep="last")
            combined.to_csv(self._cache_path(code), index=False)
        mask = (combined["date"].dt.date >= start) & (combined["date"].dt.date <= end)
        return combined.loc[mask].reset_index(drop=True)

    def histories(
        self, codes: list[str], start: date, end: date, full_window: bool = False
    ) -> tuple[dict[str, pd.DataFrame], dict[str, str]]:
        frames: dict[str, pd.DataFrame] = {}
        failures: dict[str, str] = {}
        with ThreadPoolExecutor(max_workers=self.workers) as executor:
            futures = {
                executor.submit(self.history, code, start, end, full_window): code
                for code in codes
            }
            for future in as_completed(futures):
                code = futures[future]
                try:
                    frames[code] = future.result()
                except Exception as exc:
                    failures[code] = str(exc)
                    LOG.error("证券历史行情获取失败：code=%s", code, exc_info=exc)
        self._save_metadata()
        return frames, failures

    def liquidity_histories(
        self, codes: list[str], start: date, end: date
    ) -> tuple[dict[str, pd.DataFrame], dict[str, str]]:
        """Fetch the short liquidity window with the faster Tencent endpoint."""
        frames: dict[str, pd.DataFrame] = {}
        failures: dict[str, str] = {}

        def load(code: str) -> pd.DataFrame:
            cached = self._read_cache(code)
            cached_window = cached.loc[
                (cached["date"].dt.date >= start) & (cached["date"].dt.date <= end)
            ]
            if len(cached_window) >= 3 and cached_window["date"].max().date() >= end:
                return cached_window.reset_index(drop=True)
            if self.offline:
                if len(cached_window) < 3:
                    raise DataProviderError("离线缓存不足3个流动性交易日")
                return cached_window.reset_index(drop=True)
            try:
                fetched = self._fetch_tencent(code, start, end)
            except DataProviderError as primary_error:
                try:
                    fetched = self._fetch(code, start, end)
                    self.warnings.add("流动性窗口部分使用主行情源成交额数据")
                except DataProviderError as fallback_error:
                    raise DataProviderError(
                        f"流动性主数据源失败: {primary_error}; 行情回退失败: {fallback_error}"
                    ) from fallback_error
            combined = pd.concat([cached, fetched], ignore_index=True)
            combined["date"] = pd.to_datetime(combined["date"])
            combined = combined.sort_values("date").drop_duplicates("date", keep="last")
            combined.to_csv(self._cache_path(code), index=False)
            return combined.loc[
                (combined["date"].dt.date >= start)
                & (combined["date"].dt.date <= end)
            ].reset_index(drop=True)

        with ThreadPoolExecutor(max_workers=max(self.workers, 16)) as executor:
            futures = {executor.submit(load, code): code for code in codes}
            for future in as_completed(futures):
                code = futures[future]
                try:
                    frames[code] = future.result()
                except Exception as exc:
                    failures[code] = str(exc)
                    LOG.error("证券流动性行情获取失败：code=%s", code, exc_info=exc)
        self._save_metadata()
        return frames, failures

    def security_name(self, code: str) -> str:
        return self._names.get(code, code)
