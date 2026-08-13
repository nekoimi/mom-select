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


def test_entry_candidates_reject_overheated_trends() -> None:
    config = StockStrategyConfig()
    steady = calculate_stock_metrics(
        StockSecurity("600001.XSHG", "稳健趋势", "沪深主板", 40.0, 1e8),
        _history(0.002),
        _history(0.001),
        config,
    )
    overheated = calculate_stock_metrics(
        StockSecurity("600002.XSHG", "过热趋势", "沪深主板", 80.0, 1e8),
        _history(0.02),
        _history(0.001),
        config,
    )

    result = select_entry_candidates([overheated, steady], config)

    assert [item.code for item in result] == [steady.code]
    assert steady.entry_score > 0
    assert overheated.return_20d > config.entry_max_return_20d
