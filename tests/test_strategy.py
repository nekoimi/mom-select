from datetime import date

import numpy as np
import pandas as pd

from mom_select.models import EtfMetrics, Holding, StrategyConfig
from mom_select.strategy import assess_market, calculate_metrics, choose_targets


def history(values: np.ndarray, volume: float = 1_000_000) -> pd.DataFrame:
    periods = len(values)
    dates = pd.bdate_range(end="2026-08-11", periods=periods)
    return pd.DataFrame(
        {
            "date": dates,
            "open": values,
            "close": values,
            "high": values,
            "low": values,
            "volume": np.full(periods, volume),
            "turnover": values * volume,
        }
    )


def metric(code: str, score: float) -> EtfMetrics:
    return EtfMetrics(
        code=code,
        name=code,
        momentum_score=score,
        annualized_return=score,
        r_squared=0.9,
        close=10,
        moving_average=9,
        volume_ratio=1,
        average_turnover=100_000_000,
        passed_momentum=True,
        passed_regime_filter=True,
        passed_volume=True,
        passed_loss=True,
        passed_liquidity=True,
    )


def test_market_switches_to_weak_when_three_indexes_are_below_ma() -> None:
    falling = np.linspace(12, 10, 12)
    rising = np.linspace(10, 12, 12)
    histories = {
        "a": history(falling),
        "b": history(falling),
        "c": history(falling),
        "d": history(rising),
    }
    result = assess_market(histories, {code: code for code in histories}, "normal", 10)
    assert result.regime == "weak"
    assert result.below_count == 3
    assert result.as_of == date(2026, 8, 11)


def test_market_keeps_weak_state_until_three_indexes_recover() -> None:
    flat = np.full(12, 10.0)
    falling = np.linspace(12, 10, 12)
    rising = np.linspace(10, 12, 12)
    histories = {
        "a": history(rising),
        "b": history(rising),
        "c": history(flat),
        "d": history(falling),
    }
    result = assess_market(histories, {code: code for code in histories}, "weak", 10)
    assert result.regime == "weak"


def test_metrics_accept_smooth_uptrend() -> None:
    prices = 10 * np.exp(np.linspace(0, 0.12, 40))
    result = calculate_metrics("510300.XSHG", "300ETF", history(prices), "normal", StrategyConfig())
    assert result.momentum_score > 0
    assert result.r_squared > 0.99
    assert result.passed_all


def test_current_holding_is_retained_inside_ninety_percent_band() -> None:
    config = StrategyConfig()
    eligible = [metric("leader", 1.0), metric("holding", 0.95), metric("other", 0.8)]
    candidates, targets = choose_targets(
        eligible,
        [Holding(code="holding", amount=100)],
        "normal",
        config,
    )
    assert [item.code for item in candidates] == ["leader", "holding"]
    assert targets == ["holding"]


def test_empty_selection_uses_defensive_etf() -> None:
    candidates, targets = choose_targets([], [], "normal", StrategyConfig())
    assert candidates == []
    assert targets == ["511880.XSHG"]
