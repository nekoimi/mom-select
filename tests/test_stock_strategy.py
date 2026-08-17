from dataclasses import replace

import numpy as np
import pandas as pd

from mom_select.stock.models import StockSecurity, StockStrategyConfig
from mom_select.stock.strategy import (
    calculate_stock_metrics,
    rank_stocks,
    select_entry_candidates,
    select_momentum_rankings,
)


def _history(growth: float, rows: int = 280) -> pd.DataFrame:
    close = 20 * np.exp(np.arange(rows) * growth)
    return pd.DataFrame(
        {
            "date": pd.date_range("2025-01-01", periods=rows, freq="B"),
            "open": close * 0.998,
            "close": close,
            "high": close * 1.01,
            "low": close * 0.99,
            "volume": np.full(rows, 10_000_000),
            "turnover": np.full(rows, 100_000_000),
        }
    )


def test_stock_metrics_include_multi_period_trend_and_relative_strength() -> None:
    config = StockStrategyConfig()
    security = StockSecurity("600001.XSHG", "趋势股", "沪深主板", 40.0, 1e8)

    metric = calculate_stock_metrics(security, _history(0.003), _history(0.001), config)

    assert metric.return_20d > 0
    assert metric.return_120d > metric.return_20d
    assert metric.relative_strength_60d > 0
    assert metric.r_squared > 0.99
    assert metric.ma_aligned
    assert metric.return_5d > 0
    assert metric.ma5_slope > 0
    assert metric.above_ma5


def test_rank_stocks_does_not_truncate_before_coverage_count() -> None:
    config = StockStrategyConfig(max_results=1)
    securities = [
        StockSecurity("600001.XSHG", "甲", "沪深主板", 40.0, 1e8),
        StockSecurity("600002.XSHG", "乙", "沪深主板", 50.0, 1e8),
    ]
    histories = {item.code: _history(0.003) for item in securities}

    rankings, exclusions, _ = rank_stocks(securities, histories, _history(0.001), config)

    assert len(rankings) == 2
    assert exclusions == {}


def test_stock_default_report_limits_are_twenty_and_thirty() -> None:
    config = StockStrategyConfig()

    assert config.max_results == 20
    assert config.minimum_momentum_score == 0
    assert config.entry_candidate_results == 30
    assert config.price_lower_bound_inclusive == 5
    assert config.price_upper_bound_inclusive == 55
    assert config.screen_minimum_turnover == 300_000_000
    assert config.snapshot_minimum_change == 0.01
    assert config.snapshot_maximum_change == 0.10
    assert config.snapshot_minimum_turnover_rate == 0.03
    assert config.snapshot_maximum_turnover_rate == 0.15
    assert config.trend_minimum_return_20d == 0.10
    assert config.trend_minimum_return_60d == 0.20


def test_momentum_ranking_uses_score_floor_and_maximum_limit() -> None:
    config = StockStrategyConfig(max_results=1, minimum_momentum_score=0)
    positive = calculate_stock_metrics(
        StockSecurity("600001.XSHG", "正动量", "沪深主板", 40.0, 1e8),
        _history(0.003),
        _history(0.001),
        config,
    )
    negative = calculate_stock_metrics(
        StockSecurity("600002.XSHG", "负动量", "沪深主板", 30.0, 1e8),
        _history(-0.001),
        _history(0.001),
        config,
    )

    result = select_momentum_rankings([positive, negative], config)

    assert [item.code for item in result] == [positive.code]


def test_insufficient_listing_history_is_grouped_into_one_reason() -> None:
    config = StockStrategyConfig(minimum_listing_days=250)
    security = StockSecurity("600001.XSHG", "次新样例", "沪深主板", 20.0, 1e8)

    rankings, exclusions, counts = rank_stocks(
        [security], {security.code: _history(0.003, rows=100)}, _history(0.001), config
    )

    assert rankings == []
    assert exclusions[security.code] == "上市交易日不足250日"
    assert counts == {"上市交易日不足250日": 1}


def test_entry_candidates_require_ma_alignment_and_strict_return_floors() -> None:
    config = StockStrategyConfig()
    qualifying = calculate_stock_metrics(
        StockSecurity("600001.XSHG", "趋势候选", "沪深主板", 40.0, 4e8),
        _history(0.006),
        _history(0.001),
        config,
    )
    return_20d_boundary = replace(qualifying, code="600002.XSHG", return_20d=0.10)
    return_60d_boundary = replace(qualifying, code="600003.XSHG", return_60d=0.20)
    averages_not_aligned = replace(qualifying, code="600004.XSHG", ma_aligned=False)

    result = select_entry_candidates(
        [return_20d_boundary, return_60d_boundary, averages_not_aligned, qualifying],
        config,
    )

    assert qualifying.return_20d > 0.10
    assert qualifying.return_60d > 0.20
    assert [item.code for item in result] == [qualifying.code]


def test_entry_candidates_do_not_apply_hidden_short_term_filters() -> None:
    config = StockStrategyConfig()
    metric = calculate_stock_metrics(
        StockSecurity("600001.XSHG", "中期趋势", "沪深主板", 40.0, 4e8),
        _history(0.006),
        _history(0.001),
        config,
    )
    recently_falling = replace(
        metric,
        return_5d=-0.04,
        ma5_slope=-0.01,
        above_ma5=False,
        volume_ratio=0.5,
    )

    assert select_entry_candidates([recently_falling], config) == [recently_falling]


def test_entry_score_does_not_reward_a_fresh_high_over_a_modest_pullback() -> None:
    config = StockStrategyConfig()
    high_history = _history(0.002)
    pullback_history = high_history.copy()
    pullback_history.loc[pullback_history.index[-1], "close"] *= 0.96
    pullback_history.loc[pullback_history.index[-1], "open"] *= 0.96
    pullback_history.loc[pullback_history.index[-1], "high"] *= 0.96
    pullback_history.loc[pullback_history.index[-1], "low"] *= 0.96
    high_metric = calculate_stock_metrics(
        StockSecurity("600001.XSHG", "创新高", "沪深主板", 40.0, 1e8),
        high_history,
        _history(0.001),
        config,
    )
    pullback_metric = calculate_stock_metrics(
        StockSecurity("600002.XSHG", "温和回撤", "沪深主板", 40.0, 1e8),
        pullback_history,
        _history(0.001),
        config,
    )

    assert high_metric.drawdown_from_60d_high == 0
    assert pullback_metric.drawdown_from_60d_high < 0
    assert pullback_metric.entry_score > high_metric.entry_score
