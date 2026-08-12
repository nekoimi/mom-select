from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path

from mom_select.config import (
    DEFAULT_CACHE_DIR,
    DEFAULT_POOL_FILE,
    DEFAULT_REPORT_DIR,
    DEFAULT_STATE_FILE,
    DEFAULT_STRATEGY_CONFIG,
    MARKET_INDEXES,
)
from mom_select.data import EastmoneyDataProvider
from mom_select.portfolio import load_holdings
from mom_select.reporting import write_reports
from mom_select.strategy import (
    assess_market,
    build_report,
    calculate_metrics,
    load_previous_regime,
    save_regime,
)
from mom_select.universe import load_etf_pool


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("日期格式必须为YYYY-MM-DD") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mom-select",
        description="生成本地ETF动量轮动建议，不执行交易",
    )
    parser.add_argument("--date", type=_parse_date, default=date.today(), help="行情截止日期")
    parser.add_argument("--portfolio", type=Path, help="人工维护的持仓CSV")
    parser.add_argument("--pool", type=Path, default=DEFAULT_POOL_FILE, help="ETF池CSV")
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    parser.add_argument("--state-file", type=Path, default=DEFAULT_STATE_FILE)
    parser.add_argument("--offline", action="store_true", help="只使用本地缓存，不请求网络")
    parser.add_argument("--workers", type=int, default=8, help="行情并发请求数")
    parser.add_argument(
        "--allow-incomplete-day",
        action="store_true",
        help="允许使用当日15:10前的未完成日K，仅用于数据检查",
    )
    parser.add_argument(
        "--no-save-state",
        action="store_true",
        help="不保存本次市场状态，适用于历史日期复查",
    )
    return parser


def run(args: argparse.Namespace) -> tuple[Path, Path]:
    config = DEFAULT_STRATEGY_CONFIG
    pool = load_etf_pool(args.pool)
    holdings = load_holdings(args.portfolio)
    provider = EastmoneyDataProvider(args.cache_dir, offline=args.offline, workers=args.workers)
    start = args.date - timedelta(days=180)

    index_codes = list(MARKET_INDEXES)
    index_histories, index_failures = provider.histories(index_codes, start, args.date)
    if index_failures:
        details = "；".join(f"{code}: {reason}" for code, reason in index_failures.items())
        raise RuntimeError(f"市场指数数据不完整，停止生成建议：{details}")
    previous_regime = load_previous_regime(args.state_file)
    market = assess_market(
        index_histories,
        MARKET_INDEXES,
        previous_regime,
        config.ma_lookback,
    )
    now = datetime.now().astimezone()
    if (
        market.as_of == now.date()
        and now.time() < time(15, 10)
        and not args.allow_incomplete_day
    ):
        raise RuntimeError("当日尚未到15:10，日K可能未完成；请收盘后再运行")
    stale_indexes = [
        code
        for code, frame in index_histories.items()
        if frame.empty or frame["date"].max().date() != market.as_of
    ]
    if stale_indexes:
        raise RuntimeError(
            f"市场指数交易日不一致（基准日{market.as_of}）：{', '.join(stale_indexes)}"
        )
    selected_pool = pool if market.regime == "normal" else pool.loc[pool["bucket"] == "global"]
    codes = selected_pool["code"].tolist()
    histories, failures = provider.histories(codes, start, market.as_of)
    stale_codes = [
        code
        for code in codes
        if code not in histories
        or histories[code].empty
        or histories[code]["date"].max().date() != market.as_of
    ]
    now = datetime.now().astimezone()
    if market.as_of == now.date() and now.time() >= time(15, 5):
        snapshots = provider.supplement_close_snapshots(codes, market.as_of)
        for code, snapshot in snapshots.items():
            if code in stale_codes:
                histories[code] = provider.merge_snapshot(code, snapshot)
                failures.pop(code, None)
    rankings = []
    for code, frame in histories.items():
        try:
            if frame.empty or frame["date"].max().date() != market.as_of:
                latest = "无数据" if frame.empty else str(frame["date"].max().date())
                raise ValueError(f"最新行情为{latest}，市场基准日为{market.as_of}")
            rankings.append(
                calculate_metrics(
                    code,
                    provider.security_name(code),
                    frame.loc[frame["date"].dt.date <= market.as_of],
                    market.regime,
                    config,
                )
            )
        except Exception as exc:
            failures[code] = str(exc)
    report = build_report(
        as_of=market.as_of,
        market=market,
        pool_size=len(codes),
        rankings=rankings,
        failures=failures,
        holdings=holdings,
        config=config,
        provider_warnings=sorted(provider.warnings),
    )
    if report.actionable and not args.no_save_state:
        save_regime(args.state_file, market)
    return write_reports(report, args.report_dir)


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    try:
        markdown_path, json_path = run(args)
    except Exception as exc:
        print(f"生成建议失败：{exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    print(f"ETF建议已生成：{markdown_path}")
    print(f"结构化结果：{json_path}")
    print(f"完成时间：{datetime.now().astimezone().isoformat(timespec='seconds')}")
