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
from mom_select.universe import (
    build_normal_universe,
    dynamic_universe_candidates,
    load_etf_pool,
)
from mom_select.settings import load_settings


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("日期格式必须为YYYY-MM-DD") from exc


def _intraday_volume_multiplier(quote_time: datetime) -> float:
    current = quote_time.time()
    if current < time(9, 30):
        elapsed = 0
    elif current <= time(11, 30):
        elapsed = (quote_time.hour * 60 + quote_time.minute) - (9 * 60 + 30)
    elif current < time(13, 0):
        elapsed = 120
    else:
        elapsed = 120 + (quote_time.hour * 60 + quote_time.minute) - 13 * 60
    elapsed = max(1, min(elapsed, 240))
    return 240 / elapsed


def _append_snapshot(frame, snapshot):
    import pandas as pd

    combined = pd.concat([frame, snapshot], ignore_index=True)
    combined["date"] = pd.to_datetime(combined["date"])
    return combined.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mom-select",
        description="生成本地ETF动量轮动建议，不执行交易",
    )
    parser.add_argument("--date", type=_parse_date, default=date.today(), help="行情截止日期")
    parser.add_argument("--config", type=Path, help="YAML配置文件；用于生产调度和统一路径/通知配置")
    parser.add_argument(
        "--mode",
        choices=("close", "intraday"),
        default="intraday",
        help="close=收盘复盘；intraday=13:05盘中信号",
    )
    parser.add_argument("--portfolio", type=Path, help="人工维护的持仓CSV")
    parser.add_argument("--pool", type=Path, default=DEFAULT_POOL_FILE, help="ETF池CSV")
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    parser.add_argument("--state-file", type=Path, default=DEFAULT_STATE_FILE)
    parser.add_argument("--offline", action="store_true", help="只使用本地缓存，不请求网络")
    parser.add_argument(
        "--fixed-pool-only",
        action="store_true",
        help="仅使用固定ETF池，跳过全市场动态池",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="本地调试：将盘中策略时钟固定为当日13:05",
    )
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


def run(args: argparse.Namespace):
    config = getattr(args, "strategy_config", DEFAULT_STRATEGY_CONFIG)
    pool = load_etf_pool(args.pool)
    holdings = load_holdings(args.portfolio)
    provider = EastmoneyDataProvider(args.cache_dir, offline=args.offline, workers=args.workers)
    runtime_warnings: list[str] = []
    start = args.date - timedelta(days=180)
    actual_now = datetime.now().astimezone()
    is_intraday = args.mode == "intraday"
    debug = getattr(args, "debug", False)
    historical_debug = debug and args.date < actual_now.date()
    if debug and not is_intraday:
        raise RuntimeError("DEBUG固定13:05仅支持intraday盘中模式")
    if debug and args.date > actual_now.date():
        raise RuntimeError("DEBUG模式不能模拟未来日期")
    now = (
        actual_now.replace(hour=13, minute=5, second=0, microsecond=0)
        if debug
        else actual_now
    )
    if is_intraday:
        if args.offline and not historical_debug:
            raise RuntimeError("13:05盘中模式必须在线获取实时快照，不能使用--offline")
        if not debug and args.date != now.date():
            raise RuntimeError("13:05盘中模式只能用于当前交易日，不能复查历史日期")
        if not (time(13, 5) <= now.time() <= time(13, 10, 59)):
            raise RuntimeError("13:05盘中模式仅允许在交易日13:05-13:10运行")
    uses_realtime_snapshot = is_intraday and not historical_debug
    index_history_end = (
        args.date - timedelta(days=1)
        if is_intraday
        else args.date
    )

    index_codes = list(MARKET_INDEXES)
    index_histories, index_failures = provider.histories(
        index_codes, start, index_history_end
    )
    if index_failures:
        details = "；".join(f"{code}: {reason}" for code, reason in index_failures.items())
        raise RuntimeError(f"市场指数数据不完整，停止生成建议：{details}")
    signal_time = None
    intraday_multiplier = 1.0
    if historical_debug:
        signal_time = datetime.combine(args.date, time(13, 5)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        if args.offline:
            runtime_warnings.append(
                f"离线历史DEBUG使用{args.date.isoformat()}完整日K模拟13:05；"
                "价格和成交量并非当日13:05快照"
            )
    elif is_intraday:
        signal_clock = (
            datetime.combine(args.date, time(13, 5)) if debug else now.replace(tzinfo=None)
        )
        signal_time = signal_clock.strftime("%Y-%m-%d %H:%M:%S")
        intraday_multiplier = _intraday_volume_multiplier(signal_clock)
    previous_regime = load_previous_regime(args.state_file)
    market = assess_market(
        index_histories,
        MARKET_INDEXES,
        previous_regime,
        config.ma_lookback,
    )
    signal_as_of = args.date if is_intraday else market.as_of
    if (
        not is_intraday
        and market.as_of == now.date()
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
    discovered_pool_size = 0
    dynamic_pool_size = 0
    fixed_pool_size = 0
    fixed_pool_only = getattr(args, "fixed_pool_only", False)
    if market.regime == "normal" and not fixed_pool_only:
        discovered = provider.etf_universe()
        discovered_pool_size = len(discovered)
        dynamic_candidates = dynamic_universe_candidates(discovered)
        liquidity_codes = sorted(set(discovered["code"]) | set(pool["code"]))
        completed_market_dates = sorted(
            {
                item.date()
                for frame in index_histories.values()
                for item in frame["date"]
                if item.date() < args.date
            }
        )
        if not completed_market_dates:
            raise RuntimeError("缺少信号日前的完整交易日，无法构建动态ETF池")
        liquidity_end = completed_market_dates[-1]
        liquidity_start = liquidity_end - timedelta(days=14)
        liquidity_histories, liquidity_failures = provider.liquidity_histories(
            liquidity_codes, liquidity_start, liquidity_end
        )
        discovered = discovered.copy()
        refreshed_names = discovered["code"].map(provider.security_name)
        discovered["name"] = refreshed_names.where(
            refreshed_names != discovered["code"], discovered["name"]
        )
        selection = build_normal_universe(
            pool,
            discovered,
            liquidity_histories,
            history_failures=len(liquidity_failures),
        )
        codes = selection.codes
        fixed_pool_size = len(selection.fixed_codes)
        dynamic_pool_size = len(selection.dynamic_codes)
        runtime_warnings.append(
            "ETF池：全市场发现"
            f"{discovered_pool_size}只，动态预筛{len(dynamic_candidates)}只，"
            f"固定池过滤后{fixed_pool_size}只，"
            f"动态池{dynamic_pool_size}只，合并后{len(codes)}只；"
            f"流动性门槛{selection.liquidity_threshold / 1e4:.0f}万元"
        )
        if liquidity_failures:
            runtime_warnings.append(
                f"动态池流动性数据获取失败{len(liquidity_failures)}只，已从本次选择排除"
            )
        if historical_debug:
            runtime_warnings.append(
                "历史动态池使用当前可见ETF清单回看指定日期，存在幸存者偏差"
            )
    else:
        selected_pool = (
            pool if market.regime == "normal" else pool.loc[pool["bucket"] == "global"]
        )
        codes = selected_pool["code"].tolist()
        fixed_pool_size = len(codes)
        if fixed_pool_only and market.regime == "normal":
            runtime_warnings.append("已启用--fixed-pool-only，本次未构建全市场动态ETF池")
    etf_history_end = (
        args.date - timedelta(days=1)
        if uses_realtime_snapshot or (historical_debug and not args.offline)
        else args.date
    )
    histories, failures = provider.histories(codes, start, etf_history_end)
    stale_codes = [
        code
        for code in codes
        if code not in histories
        or histories[code].empty
        or histories[code]["date"].max().date() != etf_history_end
    ]
    if historical_debug and not args.offline:
        cutoff = datetime.combine(args.date, time(13, 5))
        historical_batch = provider.historical_intraday_snapshots(codes, args.date, cutoff)
        for code, snapshot in historical_batch.frames.items():
            base = histories.get(code)
            if base is not None and not base.empty:
                histories[code] = _append_snapshot(base, snapshot)
                failures.pop(code, None)
        missing_historical_snapshots = sorted(set(codes) - set(historical_batch.frames))
        if missing_historical_snapshots:
            cached_fallbacks = provider.cached_snapshots(
                missing_historical_snapshots, args.date
            )
            for code, snapshot in cached_fallbacks.items():
                base = histories.get(code)
                if base is not None and not base.empty:
                    histories[code] = _append_snapshot(base, snapshot)
                    failures.pop(code, None)
            missing_historical_snapshots = sorted(
                set(missing_historical_snapshots) - set(cached_fallbacks)
            )
        if missing_historical_snapshots:
            runtime_warnings.append(
                f"历史13:05分钟快照缺失{len(missing_historical_snapshots)}只，"
                "且无本地当日K线，这些ETF从本次排名排除"
            )
        intraday_multiplier = _intraday_volume_multiplier(cutoff)
        runtime_warnings.append(
            f"历史13:05累计成交量按{intraday_multiplier:.3f}倍折算预计全天成交量"
        )
    elif uses_realtime_snapshot:
        etf_batch = provider.realtime_snapshots(codes, args.date)
        for code, snapshot in etf_batch.frames.items():
            quote_time = etf_batch.quote_times[code]
            if debug or time(13, 4) <= quote_time.time() <= time(13, 11):
                base = histories.get(code)
                if base is not None and not base.empty:
                    histories[code] = _append_snapshot(base, snapshot)
                    failures.pop(code, None)
        valid_snapshot_count = sum(
            code in histories
            and not histories[code].empty
            and histories[code]["date"].max().date() == args.date
            for code in codes
        )
        coverage = valid_snapshot_count / len(codes) if codes else 0.0
        if coverage < config.minimum_data_coverage:
            provider.warnings.add(
                f"13:05有效快照仅{valid_snapshot_count}/{len(codes)}，低于安全覆盖率"
            )
        if not debug:
            signal_time = max(
                (
                    item
                    for item in etf_batch.quote_times.values()
                    if item.date() == args.date
                    and time(13, 4) <= item.time() <= time(13, 11)
                ),
                default=(
                    datetime.fromisoformat(signal_time)
                    if signal_time
                    else now.replace(tzinfo=None)
                ),
            ).strftime("%Y-%m-%d %H:%M:%S")
        runtime_warnings.append(
            f"13:05累计成交量按{intraday_multiplier:.3f}倍折算预计全天成交量"
        )
    elif market.as_of == now.date() and now.time() >= time(15, 5):
        snapshots = provider.supplement_close_snapshots(codes, market.as_of)
        for code, snapshot in snapshots.items():
            if code in stale_codes:
                histories[code] = provider.merge_snapshot(code, snapshot)
                failures.pop(code, None)

    # Newly listed ETFs may have valid quotes but fewer sessions than the
    # momentum window. They cannot produce a comparable score, so exclude
    # them from this run instead of treating them as data-source failures or
    # allowing them to depress the report coverage ratio.
    minimum_history_rows = max(
        config.lookback_days + 1,
        config.ma_lookback,
        config.volume_lookback + 1,
        config.liquidity_lookback,
        4,
    )
    insufficient_history = [
        code
        for code, frame in histories.items()
        if frame is not None and not frame.empty and len(frame) < minimum_history_rows
    ]
    for code in insufficient_history:
        histories.pop(code, None)
        failures.pop(code, None)
    if insufficient_history:
        runtime_warnings.append(
            f"历史数据不足{minimum_history_rows}个交易日的ETF有{len(insufficient_history)}只，"
            "已排除，不参与动量排名"
        )
    analysis_pool_size = len(codes) - len(insufficient_history)
    rankings = []
    for code, frame in histories.items():
        try:
            if frame.empty or frame["date"].max().date() != signal_as_of:
                latest = "无数据" if frame.empty else str(frame["date"].max().date())
                raise ValueError(f"最新行情为{latest}，信号日为{signal_as_of}")
            rankings.append(
                calculate_metrics(
                    code,
                    provider.security_name(code),
                    frame.loc[frame["date"].dt.date <= signal_as_of],
                    market.regime,
                    config,
                    intraday_volume_multiplier=intraday_multiplier,
                )
            )
        except Exception as exc:
            failures[code] = str(exc)
    transient_warning_prefixes = (
        "DEBUG模式使用当前最新行情快照",
        "历史DEBUG使用",
        "13:05累计成交量按",
    )
    provider_warnings = sorted(
        item
        for item in provider.warnings
        if not item.startswith(transient_warning_prefixes)
    )
    provider_warnings.extend(runtime_warnings)
    if is_intraday:
        provider_warnings = [
            item for item in provider_warnings if "收盘行情快照补齐" not in item
        ]
    report = build_report(
        as_of=signal_as_of,
        market=market,
        pool_size=analysis_pool_size,
        rankings=rankings,
        failures=failures,
        holdings=holdings,
        config=config,
        provider_warnings=provider_warnings,
        run_mode=args.mode,
        signal_time=signal_time,
        debug=debug,
        historical_simulation=historical_debug,
        discovered_pool_size=discovered_pool_size,
        fixed_pool_size=fixed_pool_size,
        dynamic_pool_size=dynamic_pool_size,
    )
    if report.actionable and not args.no_save_state and not is_intraday:
        save_regime(args.state_file, market)
    return write_reports(report, args.report_dir)


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if args.config:
        settings = load_settings(args.config)
        args.pool = settings.pool or args.pool
        args.portfolio = settings.portfolio or args.portfolio
        args.cache_dir = settings.cache_dir or args.cache_dir
        args.report_dir = settings.report_dir or args.report_dir
        args.state_file = settings.state_file or args.state_file
        args.workers = settings.workers
        args.fixed_pool_only = settings.fixed_pool_only
        args.strategy_config = settings.strategy
    try:
        paths = run(args)
    except Exception as exc:
        print(f"生成建议失败：{exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    print(f"Markdown报告：{paths.markdown}")
    print(f"结构化结果：{paths.json}")
    print(f"HTML报告：{paths.html}")
    print(f"图片报告：{paths.image}")
    print(f"完成时间：{datetime.now().astimezone().isoformat(timespec='seconds')}")


if __name__ == "__main__":
    main()
