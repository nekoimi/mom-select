from datetime import date

import pandas as pd

from mom_select.calendar import is_trading_day, load_trading_days


def test_trading_calendar_uses_cached_dates(tmp_path):
    path = tmp_path / "calendar.csv"
    pd.DataFrame({"trade_date": ["2026-08-10", "2026-08-11"]}).to_csv(path, index=False)

    assert is_trading_day(date(2026, 8, 11), path, refresh=False)
    assert not is_trading_day(date(2026, 8, 9), path, refresh=False)


def test_trading_calendar_refreshes_cache(tmp_path, monkeypatch):
    frame = pd.DataFrame({"trade_date": pd.to_datetime(["2026-08-12"])})
    monkeypatch.setattr("akshare.tool_trade_date_hist_sina", lambda: frame)
    path = tmp_path / "calendar.csv"

    assert load_trading_days(path) == {date(2026, 8, 12)}
    assert path.exists()
