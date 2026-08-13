"""APScheduler entry point for the daily ETF signal report.

The scheduled job calls ``mom_select.cli.run`` in-process.  Supervisor only
keeps this scheduler alive; it does not spawn a second CLI process per run.
"""

from __future__ import annotations

import argparse
import logging
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from mom_select.calendar import TradingCalendarError, is_trading_day
from mom_select.cli import run
from mom_select.config import (
    DEFAULT_CACHE_DIR,
    DEFAULT_POOL_FILE,
    DEFAULT_REPORT_DIR,
    DEFAULT_STATE_FILE,
)
from mom_select.notifications import build_notifiers, notify_all
from mom_select.settings import AppSettings, load_settings


SHANGHAI = ZoneInfo("Asia/Shanghai")
LOG = logging.getLogger("mom-select-scheduler")


def build_run_args(settings: AppSettings, *, debug: bool = False) -> SimpleNamespace:
    """Build the Namespace expected by ``mom_select.cli.run``."""
    return SimpleNamespace(
        date=datetime.now(ZoneInfo(settings.schedule.timezone)).date(),
        mode="intraday",
        portfolio=settings.portfolio,
        pool=settings.pool or DEFAULT_POOL_FILE,
        cache_dir=settings.cache_dir or DEFAULT_CACHE_DIR,
        report_dir=settings.report_dir or DEFAULT_REPORT_DIR,
        state_file=settings.state_file or DEFAULT_STATE_FILE,
        offline=False,
        fixed_pool_only=settings.fixed_pool_only,
        strategy_config=settings.strategy,
        debug=debug,
        workers=settings.workers,
        allow_incomplete_day=False,
        no_save_state=True,
    )


def run_scheduled(settings: AppSettings, *, debug: bool = False) -> None:
    """Generate today's intraday report directly in this process."""
    run_args = build_run_args(settings, debug=debug)
    mode_label = "DEBUG" if debug else "正式"
    if not debug:
        calendar_path = (settings.cache_dir or DEFAULT_CACHE_DIR) / "_trading_calendar.csv"
        try:
            trading_day = is_trading_day(run_args.date, calendar_path)
        except TradingCalendarError:
            LOG.exception("交易日历不可用，为避免非交易日误执行，本次任务已跳过")
            return
        if not trading_day:
            LOG.info("%s不是交易日，本次任务跳过", run_args.date.isoformat())
            return
    LOG.info("开始执行ETF建议：%s（%s）", run_args.date.isoformat(), mode_label)
    try:
        paths = run(run_args)
    except Exception:
        LOG.exception("ETF建议执行失败")
        return
    LOG.info("报告生成完成：%s", paths.html)
    for notifier in build_notifiers(settings.notification):
        try:
            subject = f"ETF动量策略日报 {run_args.date.isoformat()}"
            text = f"报告：{paths.html}"
            if debug:
                subject = f"[DEBUG] {subject}"
                text = f"调试消息，不作为正式交易信号。\n{text}"
            notify_all([notifier], paths.image, subject, text)
            LOG.info("通知发送完成：%s", type(notifier).__name__)
        except Exception:
            LOG.exception("通知发送失败：%s", type(notifier).__name__)


def create_scheduler(settings: AppSettings) -> BlockingScheduler:
    timezone = ZoneInfo(settings.schedule.timezone)
    scheduler = BlockingScheduler(timezone=timezone)
    scheduler.add_job(
        run_scheduled,
        trigger=CronTrigger(
            day_of_week=settings.schedule.day_of_week,
            hour=settings.schedule.hour,
            minute=settings.schedule.minute,
            timezone=timezone,
        ),
        args=[settings],
        id="daily-etf-advice",
        name="工作日13:05生成ETF建议",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=settings.schedule.misfire_grace_time,
        replace_existing=True,
    )
    return scheduler


def main() -> None:
    parser = argparse.ArgumentParser(description="使用APScheduler定时生成13:05 ETF建议")
    parser.add_argument("--config", default="config/config.yaml")
    parser.add_argument("--run-once", action="store_true", help="立即调用一次任务后退出")
    parser.add_argument(
        "--debug",
        action="store_true",
        help="固定策略时间为当日13:05并发送DEBUG通知，仅可与--run-once一起使用",
    )
    args = parser.parse_args()
    if args.debug and not args.run_once:
        parser.error("--debug只能与--run-once一起使用，不能用于常驻调度")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    settings = load_settings(Path(args.config))
    if args.run_once:
        run_scheduled(settings, debug=args.debug)
        return

    scheduler = create_scheduler(settings)
    LOG.info("已启动APScheduler：工作日13:05（Asia/Shanghai）")
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        LOG.info("正在停止调度器")
        scheduler.shutdown(wait=False)


if __name__ == "__main__":
    main()
