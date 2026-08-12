from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Protocol
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd


PRICE_COLUMNS = ["date", "open", "close", "high", "low", "volume", "turnover"]


class DataProvider(Protocol):
    def history(self, code: str, start: date, end: date) -> pd.DataFrame: ...

    def histories(
        self, codes: list[str], start: date, end: date
    ) -> tuple[dict[str, pd.DataFrame], dict[str, str]]: ...

    def security_name(self, code: str) -> str: ...

    def supplement_close_snapshots(
        self, codes: list[str], trading_date: date
    ) -> dict[str, pd.DataFrame]: ...


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
    snapshot_endpoint = "https://push2.eastmoney.com/api/qt/stock/get"
    batch_snapshot_endpoint = "https://push2.eastmoney.com/api/qt/ulist.np/get"
    tencent_snapshot_endpoint = "https://qt.gtimg.cn/q="

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
        self._names: dict[str, str] = {}
        self.warnings: set[str] = set()
        self._load_metadata()

    def _load_metadata(self) -> None:
        if not self._metadata_path.exists():
            return
        try:
            payload = json.loads(self._metadata_path.read_text(encoding="utf-8"))
            self._names.update(payload.get("names", {}))
            self.warnings.update(payload.get("warnings", []))
        except (OSError, json.JSONDecodeError):
            return

    def _save_metadata(self) -> None:
        payload = {"names": self._names, "warnings": sorted(self.warnings)}
        self._metadata_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    @staticmethod
    def _secid(code: str) -> str:
        raw_code, exchange = code.split(".", maxsplit=1)
        market = "1" if exchange == "XSHG" else "0"
        return f"{market}.{raw_code}"

    def _cache_path(self, code: str) -> Path:
        return self.cache_dir / f"{code}.csv"

    @staticmethod
    def _symbol(code: str) -> str:
        raw_code, exchange = code.split(".", maxsplit=1)
        return f"{'sh' if exchange == 'XSHG' else 'sz'}{raw_code}"

    def _read_cache(self, code: str) -> pd.DataFrame:
        path = self._cache_path(code)
        if not path.exists():
            return _empty_frame()
        frame = pd.read_csv(path, parse_dates=["date"])
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
        query = urlencode(
            {
                "param": (
                    f"{symbol},day,{start.isoformat()},{end.isoformat()},1000,qfq"
                )
            }
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

    def supplement_close_snapshots(
        self, codes: list[str], trading_date: date
    ) -> dict[str, pd.DataFrame]:
        if self.offline or not codes:
            return {}
        snapshots: dict[str, pd.DataFrame] = {}
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
                    snapshot_date = datetime.strptime(values[30][:8], "%Y%m%d").date()
                    close = float(values[3])
                    open_price = float(values[5])
                    high = float(values[33])
                    low = float(values[34])
                    volume = float(values[36]) * 100
                    turnover = float(values[35].split("/")[2])
                except (ValueError, IndexError):
                    continue
                if snapshot_date != trading_date or volume <= 0:
                    continue
                self._names[code] = values[1] or code
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
            self.warnings.add("部分ETF的最新日K由腾讯批量收盘行情快照补齐")
            self._save_metadata()
        return snapshots

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
                raise DataProviderError(
                    f"主数据源失败: {primary_error}; 备用数据源失败: {fallback_error}"
                ) from fallback_error

    def history(self, code: str, start: date, end: date) -> pd.DataFrame:
        cached = self._read_cache(code)
        if self.offline:
            if cached.empty:
                raise DataProviderError("离线缓存不存在")
            combined = cached
        else:
            fetch_start = start
            if not cached.empty:
                cached_last = cached["date"].max().date()
                if cached_last >= end:
                    combined = cached
                    mask = (combined["date"].dt.date >= start) & (
                        combined["date"].dt.date <= end
                    )
                    return combined.loc[mask].reset_index(drop=True)
                fetch_start = max(start, cached_last - timedelta(days=7))
            fetched = self._fetch(code, fetch_start, end)
            combined = pd.concat([cached, fetched], ignore_index=True)
            combined["date"] = pd.to_datetime(combined["date"])
            combined = combined.sort_values("date").drop_duplicates("date", keep="last")
            combined.to_csv(self._cache_path(code), index=False)
        mask = (combined["date"].dt.date >= start) & (combined["date"].dt.date <= end)
        return combined.loc[mask].reset_index(drop=True)

    def histories(
        self, codes: list[str], start: date, end: date
    ) -> tuple[dict[str, pd.DataFrame], dict[str, str]]:
        frames: dict[str, pd.DataFrame] = {}
        failures: dict[str, str] = {}
        with ThreadPoolExecutor(max_workers=self.workers) as executor:
            futures = {executor.submit(self.history, code, start, end): code for code in codes}
            for future in as_completed(futures):
                code = futures[future]
                try:
                    frames[code] = future.result()
                except Exception as exc:
                    failures[code] = str(exc)
        self._save_metadata()
        return frames, failures

    def security_name(self, code: str) -> str:
        return self._names.get(code, code)
