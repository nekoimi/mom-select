"""APScheduler entry point for the daily ETF signal report.

The scheduled job calls ``mom_select.cli.run`` in-process.  Supervisor only
keeps this scheduler alive; it does not spawn a second CLI process per run.
"""

from __future__ import annotations

import argparse
import logging
import os
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from mom_select.cli import run
from mom_select.config import (
    DEFAULT_CACHE_DIR,
    DEFAULT_POOL_FILE,
    DEFAULT_REPORT_DIR,
    DEFAULT_STATE_FILE,
)


SHANGHAI = ZoneInfo("Asia/Shanghai")
LOG = logging.getLogger("mom-select-scheduler")


def build_run_args(args: argparse.Namespace) -> SimpleNamespace:
    """Build the Namespace expected by ``mom_select.cli.run``."""
    return SimpleNamespace(
        date=date.today(),
        mode="intraday",
        portfolio=Path(args.portfolio) if args.portfolio else None,
        pool=Path(args.pool) if args.pool else DEFAULT_POOL_FILE,
        cache_dir=Path(args.cache_dir) if args.cache_dir else DEFAULT_CACHE_DIR,
        report_dir=Path(args.report_dir) if args.report_dir else DEFAULT_REPORT_DIR,
        state_file=Path(args.state_file) if args.state_file else DEFAULT_STATE_FILE,
        offline=False,
        fixed_pool_only=args.fixed_pool_only,
        debug=False,
        workers=args.workers,
        allow_incomplete_day=False,
        no_save_state=True,
    )


def run_scheduled(args: argparse.Namespace) -> None:
    """Generate today's intraday report directly in this process."""
    run_args = build_run_args(args)
    LOG.info("开始执行ETF建议：%s", run_args.date.isoformat())
    try:
        paths = run(run_args)
    except Exception:
        LOG.exception("ETF建议执行失败")
        return
    LOG.info("报告生成完成：%s", paths.html)


def create_scheduler(args: argparse.Namespace) -> BlockingScheduler:
    scheduler = BlockingScheduler(timezone=SHANGHAI)
    scheduler.add_job(
        run_scheduled,
        trigger=CronTrigger(
            day_of_week="mon-fri",
            hour=13,
            minute=5,
            timezone=SHANGHAI,
        ),
        args=[args],
        id="daily-etf-advice",
        name="工作日13:05生成ETF建议",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=300,
        replace_existing=True,
    )
    return scheduler


def main() -> None:
    parser = argparse.ArgumentParser(description="使用APScheduler定时生成13:05 ETF建议")
    parser.add_argument("--project-dir", default=os.getcwd(), help="兼容Supervisor配置，任务在进程内执行")
    parser.add_argument("--portfolio")
    parser.add_argument("--pool")
    parser.add_argument("--cache-dir")
    parser.add_argument("--report-dir")
    parser.add_argument("--state-file")
    parser.add_argument("--fixed-pool-only", action="store_true")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--run-once", action="store_true", help="立即调用一次任务后退出")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    if args.run_once:
        run_scheduled(args)
        return

    scheduler = create_scheduler(args)
    LOG.info("已启动APScheduler：工作日13:05（Asia/Shanghai）")
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        LOG.info("正在停止调度器")
        scheduler.shutdown(wait=False)


if __name__ == "__main__":
    main()
