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

from mom_select.core.calendar import TradingCalendarError, is_trading_day
from mom_select.cli import run
from mom_select.config import (
    DEFAULT_CACHE_DIR,
    DEFAULT_POOL_FILE,
    DEFAULT_REPORT_DIR,
    DEFAULT_STATE_FILE,
)
from mom_select.core.notifications import build_notifiers, notify_all
from mom_select.settings import AppSettings, load_settings
from mom_select.stock.models import StockRunRequest
from mom_select.stock.service import run as run_stock


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
        calendar_path = settings.project_dir / "data/cache/shared/trading_calendar.csv"
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
    LOG.info(
        "报告生成完成：html=%s image=%s image_size=%d字节",
        paths.html,
        paths.image,
        paths.image.stat().st_size,
    )
    try:
        notifiers = build_notifiers(settings.notification)
    except Exception:
        LOG.exception("消息通知配置解析失败")
        return
    if not notifiers:
        LOG.info("本次没有可用通知渠道，报告生成流程结束")
        return
    LOG.info("准备发送消息通知：共%d个渠道", len(notifiers))
    for notifier in notifiers:
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


def run_stock_scheduled(settings: AppSettings) -> None:
    """Generate the stock close ranking as an isolated scheduled task."""
    task = settings.stock
    now = datetime.now(ZoneInfo(task.schedule.timezone))
    run_date = now.date()
    if now.time() < datetime.strptime("15:10", "%H:%M").time():
        LOG.warning("当日尚未到15:10，个股收盘行情可能未完成，本次任务跳过")
        return
    calendar_path = settings.project_dir / "data/cache/shared/trading_calendar.csv"
    try:
        if not is_trading_day(run_date, calendar_path):
            LOG.info("%s不是交易日，个股趋势排名任务跳过", run_date.isoformat())
            return
    except TradingCalendarError:
        LOG.exception("交易日历不可用，为避免非交易日误执行，个股任务已跳过")
        return
    LOG.info("开始执行个股趋势排名：%s（收盘）", run_date.isoformat())
    try:
        paths = run_stock(
            StockRunRequest(
                as_of=run_date,
                cache_dir=task.cache_dir,
                report_dir=task.report_dir,
                workers=settings.workers,
                strategy=task.strategy,
            )
        )
    except Exception:
        LOG.exception("个股趋势排名执行失败")
        return
    LOG.info(
        "个股报告生成完成：html=%s image=%s image_size=%d字节",
        paths.html,
        paths.image,
        paths.image.stat().st_size,
    )
    try:
        notifiers = build_notifiers(settings.notification)
    except Exception:
        LOG.exception("个股消息通知配置解析失败")
        return
    subject = f"个股趋势排名日报 {run_date.isoformat()}"
    for notifier in notifiers:
        try:
            notify_all([notifier], paths.image, subject, f"报告：{paths.html}")
            LOG.info("个股通知发送完成：%s", type(notifier).__name__)
        except Exception:
            LOG.exception("个股通知发送失败：%s", type(notifier).__name__)


def create_scheduler(settings: AppSettings) -> BlockingScheduler:
    timezone = ZoneInfo(settings.schedule.timezone)
    scheduler = BlockingScheduler(timezone=timezone)
    if settings.etf_enabled:
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
    if settings.stock.enabled:
        stock_schedule = settings.stock.schedule
        stock_timezone = ZoneInfo(stock_schedule.timezone)
        scheduler.add_job(
            run_stock_scheduled,
            trigger=CronTrigger(
                day_of_week=stock_schedule.day_of_week,
                hour=stock_schedule.hour,
                minute=stock_schedule.minute,
                timezone=stock_timezone,
            ),
            args=[settings],
            id="daily-stock-ranking",
            name="交易日收盘后生成个股趋势排名",
            max_instances=1,
            coalesce=True,
            misfire_grace_time=stock_schedule.misfire_grace_time,
            replace_existing=True,
        )
    return scheduler


def main() -> None:
    parser = argparse.ArgumentParser(description="使用APScheduler定时生成ETF建议和个股排名")
    parser.add_argument("--config", default="config/config.yaml")
    parser.add_argument("--run-once", action="store_true", help="立即调用一次任务后退出")
    parser.add_argument(
        "--task", choices=("etf", "stock", "all"), default="etf", help="单次执行的任务"
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="固定策略时间为当日13:05并发送DEBUG通知，仅可与--run-once一起使用",
    )
    args = parser.parse_args()
    if args.debug and (not args.run_once or args.task != "etf"):
        parser.error("--debug只能与--run-once --task etf一起使用")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        force=True,
    )
    LOG.info("启动mom-select调度器：config=%s run_once=%s debug=%s", args.config, args.run_once, args.debug)
    try:
        settings = load_settings(Path(args.config))
    except Exception:
        LOG.exception("配置加载失败：%s", args.config)
        raise SystemExit(1)
    LOG.info(
        "配置加载完成：timezone=%s schedule=%s %02d:%02d notifications=%s channels=%d",
        settings.schedule.timezone,
        settings.schedule.day_of_week,
        settings.schedule.hour,
        settings.schedule.minute,
        settings.notification.enabled,
        len(settings.notification.channels),
    )
    if args.run_once:
        if args.task in {"etf", "all"}:
            run_scheduled(settings, debug=args.debug)
        if args.task in {"stock", "all"}:
            run_stock_scheduled(settings)
        return

    scheduler = create_scheduler(settings)
    LOG.info(
        "已启动APScheduler：ETF=%s %s %02d:%02d（%s），个股=%s",
        settings.etf_enabled,
        settings.schedule.day_of_week,
        settings.schedule.hour,
        settings.schedule.minute,
        settings.schedule.timezone,
        settings.stock.enabled,
    )
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        LOG.info("正在停止调度器")
        scheduler.shutdown(wait=False)


if __name__ == "__main__":
    main()
