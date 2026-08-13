from __future__ import annotations

from collections import Counter

import numpy as np
import pandas as pd

from mom_select.core.momentum import calculate_momentum
from mom_select.stock.models import StockMetrics, StockSecurity, StockStrategyConfig


def _period_return(closes: np.ndarray, window: int) -> float:
    if len(closes) < window + 1 or closes[-window - 1] <= 0:
        raise ValueError(f"缺少{window}日收益计算数据")
    return float(closes[-1] / closes[-window - 1] - 1)


def calculate_stock_metrics(
    security: StockSecurity,
    frame: pd.DataFrame,
    benchmark: pd.DataFrame,
    config: StockStrategyConfig,
) -> StockMetrics:
    max_window = max(config.trend_windows)
    required_rows = max(
        max_window + 1,
        config.minimum_listing_days,
        config.liquidity_lookback,
        21,
    )
    clean = frame.sort_values("date").dropna(
        subset=["open", "close", "high", "low", "turnover"]
    )
    benchmark_clean = benchmark.sort_values("date").dropna(subset=["close"])
    if len(clean) < required_rows:
        raise ValueError(f"仅有{len(clean)}个有效交易日，至少需要{required_rows}个")
    if len(benchmark_clean) < 61:
        raise ValueError("基准指数不足61个有效交易日")

    closes = clean["close"].to_numpy(dtype=float)
    benchmark_closes = benchmark_clean["close"].to_numpy(dtype=float)
    returns = {window: _period_return(closes, window) for window in config.trend_windows}
    for required in (20, 60, 120):
        if required not in returns:
            returns[required] = _period_return(closes, required)
    benchmark_20d = _period_return(benchmark_closes, 20)
    benchmark_60d = _period_return(benchmark_closes, 60)
    relative_20d = returns[20] - benchmark_20d
    relative_60d = returns[60] - benchmark_60d
    _, _, r_squared = calculate_momentum(closes, 60)

    previous_closes = clean["close"].shift(1)
    true_range = pd.concat(
        [
            clean["high"] - clean["low"],
            (clean["high"] - previous_closes).abs(),
            (clean["low"] - previous_closes).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr_ratio = float(true_range.tail(14).mean() / closes[-1])
    recent = closes[-(max_window + 1) :]
    running_max = np.maximum.accumulate(recent)
    max_drawdown = float(np.min(recent / running_max - 1))
    average_turnover = float(clean["turnover"].tail(config.liquidity_lookback).mean())
    ma20 = float(np.mean(closes[-20:]))
    ma60 = float(np.mean(closes[-60:]))
    ma120 = float(np.mean(closes[-120:]))
    ma_aligned = bool(closes[-1] > ma20 > ma60 > ma120)
    distance_ma20 = float(closes[-1] / ma20 - 1)
    drawdown_from_60d_high = float(closes[-1] / np.max(closes[-60:]) - 1)

    raw_trend = (
        0.20 * returns[20]
        + 0.30 * returns[60]
        + 0.25 * returns[120]
        + 0.15 * relative_20d
        + 0.10 * relative_60d
    )
    quality = 0.5 + 0.5 * max(0.0, min(1.0, r_squared))
    risk_penalty = 0.8 * atr_ratio + 0.2 * abs(max_drawdown)
    score = 100 * (raw_trend * quality - risk_penalty)
    trend_quality = max(0.0, min(1.0, r_squared))
    relative_quality = max(0.0, min(1.0, relative_60d / 0.30))
    ma20_quality = max(0.0, 1 - abs(distance_ma20) / config.entry_max_distance_ma20)
    high_quality = max(
        0.0,
        1 - abs(drawdown_from_60d_high) / config.entry_max_drawdown_from_60d_high,
    )
    heat_quality = max(
        0.0,
        1 - max(0.0, returns[20]) / config.entry_max_return_20d,
    )
    entry_score = 100 * (
        0.30 * trend_quality
        + 0.25 * relative_quality
        + 0.20 * ma20_quality
        + 0.15 * high_quality
        + 0.10 * heat_quality
    )
    return StockMetrics(
        code=security.code,
        name=security.name,
        close=security.price,
        score=float(score),
        return_20d=returns[20],
        return_60d=returns[60],
        return_120d=returns[120],
        relative_strength_20d=relative_20d,
        relative_strength_60d=relative_60d,
        r_squared=float(r_squared),
        atr_ratio=atr_ratio,
        max_drawdown=max_drawdown,
        average_turnover=average_turnover,
        ma_aligned=ma_aligned,
        distance_ma20=distance_ma20,
        drawdown_from_60d_high=drawdown_from_60d_high,
        entry_score=float(entry_score),
    )


def select_entry_candidates(
    rankings: list[StockMetrics], config: StockStrategyConfig
) -> list[StockMetrics]:
    candidates = [
        item
        for item in rankings
        if item.ma_aligned
        and item.relative_strength_20d > 0
        and item.relative_strength_60d > 0
        and config.entry_min_return_20d <= item.return_20d <= config.entry_max_return_20d
        and 0 <= item.distance_ma20 <= config.entry_max_distance_ma20
        and item.r_squared >= config.entry_min_r_squared
        and item.atr_ratio <= config.entry_max_atr_ratio
        and item.drawdown_from_60d_high >= -config.entry_max_drawdown_from_60d_high
    ]
    candidates.sort(key=lambda item: (-item.entry_score, -item.score, item.code))
    return candidates[: config.entry_candidate_results]


def select_momentum_rankings(
    rankings: list[StockMetrics], config: StockStrategyConfig
) -> list[StockMetrics]:
    qualified = [
        item for item in rankings if item.score >= config.minimum_momentum_score
    ]
    return qualified[: config.max_results]


def rank_stocks(
    securities: list[StockSecurity],
    histories: dict[str, pd.DataFrame],
    benchmark: pd.DataFrame,
    config: StockStrategyConfig,
) -> tuple[list[StockMetrics], dict[str, str], dict[str, int]]:
    rankings: list[StockMetrics] = []
    exclusions: dict[str, str] = {}
    counts: Counter[str] = Counter()
    for security in securities:
        frame = histories.get(security.code)
        if frame is None or frame.empty:
            continue
        try:
            metric = calculate_stock_metrics(security, frame, benchmark, config)
            if metric.average_turnover < config.minimum_average_turnover:
                reason = "平均成交额不足"
            elif metric.atr_ratio > config.max_atr_ratio:
                reason = "ATR波动率过高"
            else:
                rankings.append(metric)
                continue
        except Exception as exc:
            detail = str(exc)
            reason = (
                f"上市交易日不足{config.minimum_listing_days}日"
                if detail.startswith("仅有") and "个有效交易日" in detail
                else detail
            )
        exclusions[security.code] = reason
        counts[reason] += 1
    rankings.sort(key=lambda item: (-item.score, item.code))
    return rankings, exclusions, dict(counts)
