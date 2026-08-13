from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd


class TradingCalendarError(RuntimeError):
    """Raised when the exchange trading calendar cannot be loaded."""


def _read_cache(path: Path) -> set[date]:
    if not path.exists():
        return set()
    try:
        frame = pd.read_csv(path, usecols=["trade_date"], parse_dates=["trade_date"])
    except (OSError, ValueError, pd.errors.ParserError):
        return set()
    return {item.date() for item in frame["trade_date"].dropna()}


def load_trading_days(path: Path, *, refresh: bool = True) -> set[date]:
    """Load China's exchange calendar, refreshing the local cache with AKShare."""
    cached = _read_cache(path)
    if refresh:
        try:
            import akshare as ak

            frame = ak.tool_trade_date_hist_sina()
            column = "trade_date" if "trade_date" in frame.columns else frame.columns[0]
            dates = pd.to_datetime(frame[column], errors="coerce").dropna().dt.normalize()
            if not dates.empty:
                path.parent.mkdir(parents=True, exist_ok=True)
                pd.DataFrame({"trade_date": dates}).drop_duplicates().to_csv(path, index=False)
                return {item.date() for item in dates}
        except Exception:
            pass
    if cached:
        return cached
    raise TradingCalendarError("无法获取交易日历，且本地没有可用交易日历缓存")


def is_trading_day(day: date, path: Path, *, refresh: bool = True) -> bool:
    days = load_trading_days(path, refresh=refresh)
    if day > max(days):
        raise TradingCalendarError(f"交易日历仅覆盖至{max(days).isoformat()}，无法判断{day}")
    return day in days
