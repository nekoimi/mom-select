from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from mom_select.etf import backtest
from mom_select.etf.backtest import ExecutionConfig, run_backtest, write_backtest
from mom_select.models import EtfMetrics, StrategyConfig


def history(dates: pd.DatetimeIndex, closes: list[float]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": dates,
            "open": closes,
            "close": closes,
            "high": closes,
            "low": closes,
            "volume": [1_000_000] * len(dates),
            "turnover": [100_000_000] * len(dates),
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


def test_backtest_executes_close_signal_at_next_open(monkeypatch) -> None:
    dates = pd.bdate_range("2026-01-01", periods=45)
    switch_date = dates[-3].date()
    pool = pd.DataFrame(
        {"code": ["a", "b"], "bucket": ["china", "china"]}
    )
    histories = {
        "a": history(dates, [100.0 + index for index in range(len(dates))]),
        "b": history(dates, [200.0 + index * 2 for index in range(len(dates))]),
    }
    index_histories = {
        code: history(dates, [100.0 + index for index in range(len(dates))])
        for code in backtest.MARKET_INDEXES
    }

    def fake_metrics(code, name, frame, regime, config):
        current = frame["date"].iloc[-1].date()
        leader = "a" if current < switch_date else "b"
        return metric(code, 1.0 if code == leader else 0.5)

    monkeypatch.setattr(backtest, "calculate_metrics", fake_metrics)
    result = run_backtest(
        pool,
        histories,
        index_histories,
        {"a": "A", "b": "B"},
        dates[-3].date(),
        dates[-1].date(),
        StrategyConfig(min_average_turnover=0),
        ExecutionConfig(fixed_fee=0, slippage_rate=0, impact_rate=0, premium_rate=0, lot_size=1),
    )

    assert result.records[0].held_codes == ["a"]
    assert result.records[0].next_targets == ["b"]
    assert result.records[0].action == "换仓"
    assert result.records[1].held_codes == ["b"]
    assert result.records[0].daily_return == 0
    assert [trade.side for trade in result.trades[:3]] == ["buy", "sell", "buy"]
    assert result.trades[1].date == dates[-2].date()
    assert result.records[0].benchmark_daily_return == 0
    expected_value = 1.0
    for record in result.records:
        expected_value *= 1 + record.daily_return
    assert result.final_net_value == expected_value
    assert result.final_return == expected_value - 1
    assert result.switch_count == 1
    assert result.operation_count == 3
    assert result.max_drawdown <= 0


def test_write_backtest_outputs_daily_csv_and_json(tmp_path: Path, monkeypatch) -> None:
    dates = pd.bdate_range("2026-01-01", periods=40)
    pool = pd.DataFrame({"code": ["a"], "bucket": ["china"]})
    histories = {"a": history(dates, [100.0] * len(dates))}
    index_histories = {
        code: history(dates, [100.0 + index for index in range(len(dates))])
        for code in backtest.MARKET_INDEXES
    }
    monkeypatch.setattr(
        backtest, "calculate_metrics", lambda code, name, frame, regime, config: metric(code, 1)
    )
    result = run_backtest(
        pool,
        histories,
        index_histories,
        {"a": "A"},
        dates[-2].date(),
        dates[-1].date(),
        StrategyConfig(defensive_etf="a", min_average_turnover=0),
        ExecutionConfig(fixed_fee=0, slippage_rate=0, impact_rate=0, premium_rate=0, lot_size=1),
    )

    paths = write_backtest(result, tmp_path)

    daily = pd.read_csv(paths.csv)
    assert daily["held_codes"].tolist() == ["a", "a"]
    assert daily["daily_return"].tolist() == [0.0, 0.0]
    assert "benchmark_cumulative_return" in daily.columns
    assert paths.json.exists()
    assert paths.trades_csv.exists()
    assert paths.html.exists()
    html = paths.html.read_text(encoding="utf-8")
    assert "<canvas id=\"chart\">" in html
    assert "累计收益率" in html
    assert "资产金额" in html
    assert "up:'#d45555',down:'#24906c'" in html
    assert ".positive { color: #c43b3b; } .negative { color: #087f5b; }" in html
    assert "交易费用明细" in html
    assert "固定手续费" in html
    assert "ETF 溢价折价成本" in html
    assert paths.image.exists()
    assert paths.image.stat().st_size > 1_000


def test_backtest_applies_full_position_lots_and_execution_costs(monkeypatch) -> None:
    dates = pd.bdate_range("2026-01-01", periods=40)
    pool = pd.DataFrame({"code": ["a"], "bucket": ["china"]})
    histories = {"a": history(dates, [10.0] * len(dates))}
    index_histories = {
        code: history(dates, [100.0] * len(dates)) for code in backtest.MARKET_INDEXES
    }
    monkeypatch.setattr(
        backtest, "calculate_metrics", lambda code, name, frame, regime, config: metric(code, 1)
    )

    result = run_backtest(
        pool,
        histories,
        index_histories,
        {"a": "A"},
        dates[-1].date(),
        dates[-1].date(),
        StrategyConfig(defensive_etf="a", min_average_turnover=0),
        ExecutionConfig(),
    )

    trade = result.trades[0]
    assert trade.side == "buy"
    assert trade.shares == 900
    assert trade.fixed_fee == 5
    assert trade.slippage_cost == 4.5
    assert trade.impact_cost == 1.8
    assert trade.premium_cost == 9
    assert trade.total_cost == 20.3
    assert result.records[0].cash == pytest.approx(979.7)
    assert result.final_portfolio_value == pytest.approx(9_979.7)
    assert result.final_return == pytest.approx(-0.00203)


def test_backtest_uses_cash_defense_when_one_lot_is_unaffordable(monkeypatch) -> None:
    dates = pd.bdate_range("2026-01-01", periods=40)
    pool = pd.DataFrame({"code": ["a"], "bucket": ["global"]})
    histories = {"a": history(dates, [100.0] * len(dates))}
    index_histories = {
        code: history(dates, [100.0] * len(dates)) for code in backtest.MARKET_INDEXES
    }
    monkeypatch.setattr(
        backtest, "calculate_metrics", lambda code, name, frame, regime, config: metric(code, 1)
    )

    result = run_backtest(
        pool,
        histories,
        index_histories,
        {"a": "A"},
        dates[-1].date(),
        dates[-1].date(),
        StrategyConfig(defensive_etf="a", min_average_turnover=0),
        ExecutionConfig(initial_capital=9_000),
    )

    record = result.records[0]
    assert record.cash_defense
    assert record.unfilled_target == "a"
    assert record.action == "现金防御"
    assert record.held_codes == []
    assert record.daily_return == 0
    assert result.trades == []
    assert result.cash_defense_days == 1
    assert result.final_portfolio_value == 9_000


def test_backtest_rejects_reversed_date_range() -> None:
    pool = pd.DataFrame({"code": [], "bucket": []})
    try:
        run_backtest(pool, {}, {}, {}, date(2026, 2, 1), date(2026, 1, 1))
    except ValueError as exc:
        assert "开始日期" in str(exc)
    else:
        raise AssertionError("应拒绝倒置的日期区间")
