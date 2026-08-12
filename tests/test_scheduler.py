from datetime import datetime
from pathlib import Path

from apscheduler.triggers.cron import CronTrigger

from scripts.mom_select_scheduler import (
    SHANGHAI,
    build_run_args,
    create_scheduler,
)
from mom_select.settings import AppSettings, ScheduleSettings


def test_build_run_args_targets_intraday_mode():
    args = build_run_args(AppSettings(portfolio=Path("/opt/mom-select/config/portfolio.csv")))
    assert args.mode == "intraday"
    assert args.no_save_state is True
    assert args.portfolio == Path("/opt/mom-select/config/portfolio.csv")


def test_scheduler_has_weekday_1305_trigger():
    scheduler = create_scheduler(AppSettings(schedule=ScheduleSettings()))
    job = scheduler.get_job("daily-etf-advice")
    assert job is not None
    assert isinstance(job.trigger, CronTrigger)
    next_fire = job.trigger.get_next_fire_time(
        None, datetime(2026, 8, 12, 12, 0, tzinfo=SHANGHAI)
    )
    assert next_fire == datetime(2026, 8, 12, 13, 5, tzinfo=SHANGHAI)
