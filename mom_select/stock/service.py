from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime, timedelta

from mom_select.data import EastmoneyDataProvider
from mom_select.stock.data import AkshareStockDataProvider
from mom_select.stock.models import StockAdviceReport, StockRunRequest
from mom_select.stock.reporting import write_reports
from mom_select.stock.strategy import (
    rank_stocks,
    select_entry_candidates,
    select_momentum_rankings,
)
from mom_select.stock.universe import prefilter_stock_universe


LOG = logging.getLogger("mom-select.stock.service")


def _public_data_warnings(
    provider_warnings: list[str], benchmark_warnings: list[str]
) -> list[str]:
    """Turn detailed provider diagnostics into report-safe summaries."""
    warnings: list[str] = []
    universe_fallback = next(
        (item for item in provider_warnings if item.startswith("全市场A股快照已回退到")),
        None,
    )
    if universe_fallback:
        source = universe_fallback.split("回退到", 1)[1].split("；", 1)[0]
        warnings.append(f"全市场A股快照使用{source}备用数据源")
    elif any("使用当日本地缓存" in item for item in provider_warnings):
        warnings.append("全市场A股快照更新失败，本次使用同交易日本地缓存")

    history_cache_count = sum(
        "前复权日线更新失败，使用缓存" in item for item in provider_warnings
    )
    if history_cache_count:
        warnings.append(f"{history_cache_count}只股票的前复权日线使用本地缓存")
    history_fallback_count = sum(
        "前复权日线已回退到" in item for item in provider_warnings
    )
    if history_fallback_count:
        warnings.append(f"{history_fallback_count}只股票的前复权日线使用备用数据源")
    if benchmark_warnings:
        warnings.append("市场基准指数行情使用备用数据源或缓存")
    return warnings


def run(request: StockRunRequest):
    config = request.strategy
    provider = AkshareStockDataProvider(
        request.cache_dir, offline=request.offline, workers=request.workers
    )
    LOG.info("获取全市场A股快照: date=%s", request.as_of.isoformat())
    universe = provider.stock_universe(request.as_of)
    if len(universe) < config.minimum_universe_size:
        raise RuntimeError(
            f"全市场A股清单仅{len(universe)}只，低于完整性门槛"
            f"{config.minimum_universe_size}只"
        )
    securities, exclusions, exclusion_counts = prefilter_stock_universe(universe, config)
    LOG.info(
        "全市场必要条件预筛完成: universe=%d selected=%d excluded=%d",
        len(universe),
        len(securities),
        len(exclusions),
    )
    if not securities:
        raise RuntimeError("全市场A股没有股票通过必要筛选条件")

    start = request.as_of - timedelta(days=max(int(config.minimum_listing_days * 1.8), 540))
    histories, failures = provider.histories(
        [security.code for security in securities], start, request.as_of
    )
    stale_codes = [
        code
        for code, frame in histories.items()
        if frame.empty or frame["date"].max().date() != request.as_of
    ]
    for code in stale_codes:
        frame = histories.pop(code)
        latest = "无数据" if frame.empty else frame["date"].max().date().isoformat()
        failures[code] = f"前复权日线未覆盖目标交易日，最新为{latest}"
    benchmark_cache = request.cache_dir / "benchmark"
    benchmark_provider = EastmoneyDataProvider(
        benchmark_cache, offline=request.offline, workers=1
    )
    benchmark = benchmark_provider.history(config.benchmark, start, request.as_of)
    if benchmark.empty or benchmark["date"].max().date() != request.as_of:
        latest = "无数据" if benchmark.empty else benchmark["date"].max().date().isoformat()
        raise RuntimeError(f"个股排名基准指数未覆盖{request.as_of}: 最新为{latest}")

    all_rankings, metric_exclusions, metric_counts = rank_stocks(
        securities, histories, benchmark, config
    )
    exclusions.update(metric_exclusions)
    counts = Counter(exclusion_counts)
    counts.update(metric_counts)
    analyzed_count = len(all_rankings) + len(metric_exclusions)
    data_coverage = analyzed_count / len(securities) if securities else 0.0
    provider_diagnostics = list(provider.warnings)
    benchmark_diagnostics = sorted(benchmark_provider.warnings)
    for diagnostic in [*provider_diagnostics, *benchmark_diagnostics]:
        LOG.warning("个股数据诊断: %s", diagnostic)
    warnings = _public_data_warnings(provider_diagnostics, benchmark_diagnostics)
    if failures:
        warnings.append(f"{len(failures)}只股票历史行情获取失败")
    rankings = select_momentum_rankings(all_rankings, config)
    entry_candidates = select_entry_candidates(all_rankings, config)
    actionable = data_coverage >= config.minimum_data_coverage and bool(all_rankings)
    if data_coverage < config.minimum_data_coverage:
        warnings.append(
            f"候选历史数据覆盖率仅{data_coverage:.1%}，低于"
            f"{config.minimum_data_coverage:.0%}，本次结果不可执行"
        )
    if not all_rankings:
        warnings.append("没有股票完成趋势排名")
    report = StockAdviceReport(
        generated_at=datetime.now().astimezone().isoformat(timespec="seconds"),
        as_of=request.as_of,
        universe_size=len(universe),
        prefiltered_size=len(securities),
        analyzed_count=analyzed_count,
        rankings=rankings,
        entry_candidates=entry_candidates,
        exclusion_counts=dict(counts),
        exclusions=exclusions,
        failed_codes=failures,
        warnings=warnings,
        actionable=actionable,
        momentum_limit=config.max_results,
        entry_candidate_limit=config.entry_candidate_results,
    )
    LOG.info(
        "个股趋势排名完成: analyzed=%d momentum=%d entry_candidates=%d actionable=%s",
        analyzed_count,
        len(rankings),
        len(entry_candidates),
        actionable,
    )
    return write_reports(report, request.report_dir)
