from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime, time
from pathlib import Path

from mom_select.settings import load_settings
from mom_select.stock.models import StockRunRequest, StockStrategyConfig
from mom_select.stock.service import run


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("日期格式必须为YYYY-MM-DD") from exc


def build_parser(prog: str = "mom-select stock") -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=prog, description="生成个股趋势排名，不执行交易")
    parser.add_argument("--date", type=_parse_date, default=date.today(), help="收盘行情日期")
    parser.add_argument("--config", type=Path, help="YAML配置文件")
    parser.add_argument("--cache-dir", type=Path, default=Path("data/cache/stock"))
    parser.add_argument("--report-dir", type=Path, default=Path("reports/stock"))
    parser.add_argument(
        "--offline",
        action="store_true",
        help="只使用目标日期全市场快照和历史行情缓存，不联网补数",
    )
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument(
        "--allow-incomplete-day", action="store_true", help="允许15:10前运行，仅用于数据检查"
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    strategy = StockStrategyConfig()
    if args.config:
        settings = load_settings(args.config)
        stock = settings.stock
        args.cache_dir = stock.cache_dir
        args.report_dir = stock.report_dir
        args.workers = settings.workers
        strategy = stock.strategy
    now = datetime.now().astimezone()
    if args.date > now.date():
        print("生成个股排名失败：行情日期不能晚于当前日期", file=sys.stderr)
        raise SystemExit(1)
    if args.date == now.date() and now.time() < time(15, 10) and not args.allow_incomplete_day:
        print("生成个股排名失败：当日尚未到15:10，收盘行情可能未完成", file=sys.stderr)
        raise SystemExit(1)
    try:
        paths = run(
            StockRunRequest(
                as_of=args.date,
                cache_dir=args.cache_dir,
                report_dir=args.report_dir,
                offline=args.offline,
                workers=args.workers,
                strategy=strategy,
            )
        )
    except Exception as exc:
        logging.getLogger("mom-select.stock.cli").exception("个股趋势排名生成失败")
        print(f"生成个股排名失败：{exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    print(f"Markdown报告：{paths.markdown}")
    print(f"结构化结果：{paths.json}")
    print(f"HTML报告：{paths.html}")
    print(f"图片报告：{paths.image}")


if __name__ == "__main__":
    main()
