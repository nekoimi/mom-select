from datetime import datetime
from pathlib import Path

from apscheduler.triggers.cron import CronTrigger

from scripts.mom_select_scheduler import (
    SHANGHAI,
    build_run_args,
    create_scheduler,
    run_scheduled,
    run_stock_scheduled,
)
from mom_select.settings import AppSettings, ScheduleSettings
from mom_select.settings import StockTaskSettings


def test_build_run_args_targets_intraday_mode():
    args = build_run_args(AppSettings(portfolio=Path("/opt/mom-select/config/portfolio.csv")))
    assert args.mode == "intraday"
    assert args.no_save_state is True
    assert args.portfolio == Path("/opt/mom-select/config/portfolio.csv")


def test_build_run_args_supports_debug_notification_run():
    args = build_run_args(AppSettings(), debug=True)
    assert args.debug is True
    assert args.no_save_state is True


def test_scheduler_has_weekday_1305_trigger():
    scheduler = create_scheduler(AppSettings(schedule=ScheduleSettings()))
    job = scheduler.get_job("daily-etf-advice")
    assert job is not None
    assert isinstance(job.trigger, CronTrigger)
    next_fire = job.trigger.get_next_fire_time(
        None, datetime(2026, 8, 12, 12, 0, tzinfo=SHANGHAI)
    )
    assert next_fire == datetime(2026, 8, 12, 13, 5, tzinfo=SHANGHAI)


def test_scheduler_can_register_independent_stock_close_job():
    settings = AppSettings(
        stock=StockTaskSettings(enabled=True, schedule=ScheduleSettings(hour=15, minute=20))
    )
    scheduler = create_scheduler(settings)
    job = scheduler.get_job("daily-stock-ranking")
    assert job is not None
    next_fire = job.trigger.get_next_fire_time(
        None, datetime(2026, 8, 12, 12, 0, tzinfo=SHANGHAI)
    )
    assert next_fire == datetime(2026, 8, 12, 15, 20, tzinfo=SHANGHAI)


def test_scheduler_can_disable_etf_job():
    scheduler = create_scheduler(AppSettings(etf_enabled=False))

    assert scheduler.get_job("daily-etf-advice") is None


def test_scheduled_run_skips_non_trading_day(monkeypatch):
    called = False

    def fake_run(args):
        nonlocal called
        called = True

    monkeypatch.setattr("scripts.mom_select_scheduler.is_trading_day", lambda *args: False)
    monkeypatch.setattr("scripts.mom_select_scheduler.run", fake_run)

    run_scheduled(AppSettings())

    assert not called


def test_stock_scheduled_run_skips_non_trading_day(monkeypatch):
    called = False

    def fake_run(request):
        nonlocal called
        called = True

    monkeypatch.setattr("scripts.mom_select_scheduler.is_trading_day", lambda *args: False)
    monkeypatch.setattr("scripts.mom_select_scheduler.run_stock", fake_run)

    run_stock_scheduled(AppSettings(stock=StockTaskSettings(enabled=True)))

    assert not called
