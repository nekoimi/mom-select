"""Shared trading-calendar API.

The implementation remains import-compatible at ``mom_select.calendar``
during the staged package migration.
"""

from mom_select.calendar import TradingCalendarError, is_trading_day, load_trading_days

__all__ = ["TradingCalendarError", "is_trading_day", "load_trading_days"]

