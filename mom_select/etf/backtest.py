from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from mom_select.config import (
    DEFAULT_CACHE_DIR,
    DEFAULT_POOL_FILE,
    DEFAULT_REPORT_DIR,
    DEFAULT_STRATEGY_CONFIG,
    MARKET_INDEXES,
)
from mom_select.data import EastmoneyDataProvider
from mom_select.models import Holding, Regime, StrategyConfig
from mom_select.settings import load_settings
from mom_select.strategy import assess_market, calculate_metrics, choose_targets
from mom_select.universe import load_etf_pool


@dataclass(frozen=True)
class ExecutionConfig:
    initial_capital: float = 10_000.0
    fixed_fee: float = 5.0
    slippage_rate: float = 0.0005
    impact_rate: float = 0.0002
    premium_rate: float = 0.001
    lot_size: int = 100

    def validate(self) -> None:
        if self.initial_capital <= 0:
            raise ValueError("起步资金必须大于0")
        if self.fixed_fee < 0:
            raise ValueError("每笔手续费不能为负数")
        if min(self.slippage_rate, self.impact_rate, self.premium_rate) < 0:
            raise ValueError("滑点、冲击成本和溢价参数不能为负数")
        if self.slippage_rate + self.impact_rate + self.premium_rate >= 1:
            raise ValueError("单边成交成本必须低于100%")
        if self.lot_size <= 0:
            raise ValueError("交易单位必须为正整数")

    @property
    def variable_cost_rate(self) -> float:
        return self.slippage_rate + self.impact_rate + self.premium_rate


@dataclass(frozen=True)
class BacktestTrade:
    date: date
    side: str
    code: str
    name: str
    market_price: float
    execution_price: float
    shares: int
    market_notional: float
    fixed_fee: float
    slippage_cost: float
    impact_cost: float
    premium_cost: float
    total_cost: float


@dataclass(frozen=True)
class BacktestDay:
    date: date
    held_codes: list[str]
    held_names: list[str]
    daily_return: float
    cumulative_return: float
    benchmark_daily_return: float
    benchmark_cumulative_return: float
    regime: Regime
    action: str
    next_targets: list[str]
    portfolio_value: float
    cash: float
    position_shares: int
    trading_cost: float
    cash_defense: bool
    unfilled_target: str | None


@dataclass(frozen=True)
class BacktestResult:
    requested_start: date
    requested_end: date
    actual_start: date
    actual_end: date
    trading_days: int
    final_return: float
    final_net_value: float
    annualized_return: float
    max_drawdown: float
    win_rate: float
    switch_count: int
    benchmark_final_return: float
    initial_capital: float
    final_portfolio_value: float
    total_trade_cost: float
    operation_count: int
    cash_defense_days: int
    execution: ExecutionConfig
    records: list[BacktestDay]
    trades: list[BacktestTrade]

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class BacktestPaths:
    csv: Path
    trades_csv: Path
    json: Path
    html: Path
    image: Path


def _required_history_rows(config: StrategyConfig) -> int:
    return max(
        config.lookback_days + 1,
        config.ma_lookback,
        config.volume_lookback + 1,
        config.liquidity_lookback,
        4,
    )


def _common_market_dates(index_histories: dict[str, pd.DataFrame]) -> list[date]:
    date_sets = []
    for code in MARKET_INDEXES:
        frame = index_histories.get(code)
        if frame is None or frame.empty:
            raise ValueError(f"市场指数{code}没有可用行情")
        date_sets.append(set(frame["date"].dt.date))
    return sorted(set.intersection(*date_sets))


def _close_on_or_before(frame: pd.DataFrame, trading_date: date) -> float | None:
    available = frame.loc[frame["date"].dt.date <= trading_date, "close"]
    if available.empty:
        return None
    return float(available.iloc[-1])


def _price_on_date(frame: pd.DataFrame, trading_date: date, column: str) -> float | None:
    available = frame.loc[frame["date"].dt.date == trading_date, column].dropna()
    if available.empty:
        return None
    value = float(available.iloc[-1])
    return value if value > 0 else None


def _build_trade(
    trading_date: date,
    side: str,
    code: str,
    name: str,
    market_price: float,
    shares: int,
    execution: ExecutionConfig,
) -> BacktestTrade:
    direction = 1 if side == "buy" else -1
    execution_price = market_price * (1 + direction * execution.variable_cost_rate)
    market_notional = market_price * shares
    slippage_cost = market_notional * execution.slippage_rate
    impact_cost = market_notional * execution.impact_rate
    premium_cost = market_notional * execution.premium_rate
    return BacktestTrade(
        date=trading_date,
        side=side,
        code=code,
        name=name,
        market_price=market_price,
        execution_price=execution_price,
        shares=shares,
        market_notional=market_notional,
        fixed_fee=execution.fixed_fee,
        slippage_cost=slippage_cost,
        impact_cost=impact_cost,
        premium_cost=premium_cost,
        total_cost=execution.fixed_fee + slippage_cost + impact_cost + premium_cost,
    )


def _targets_for_date(
    trading_date: date,
    regime: Regime,
    current_targets: list[str],
    pool: pd.DataFrame,
    histories: dict[str, pd.DataFrame],
    names: dict[str, str],
    config: StrategyConfig,
) -> list[str]:
    selected_pool = pool if regime == "normal" else pool.loc[pool["bucket"] == "global"]
    rankings = []
    required_rows = _required_history_rows(config)
    for code in selected_pool["code"]:
        frame = histories.get(code)
        if frame is None or frame.empty:
            continue
        history = frame.loc[frame["date"].dt.date <= trading_date]
        if len(history) < required_rows or history["date"].max().date() != trading_date:
            continue
        try:
            rankings.append(
                calculate_metrics(code, names.get(code, code), history, regime, config)
            )
        except ValueError:
            continue
    rankings.sort(key=lambda item: item.momentum_score, reverse=True)
    eligible = [item for item in rankings if item.passed_all]
    holdings = [Holding(code=code, amount=1) for code in current_targets]
    _, targets = choose_targets(eligible, holdings, regime, config)
    return targets


def run_backtest(
    pool: pd.DataFrame,
    histories: dict[str, pd.DataFrame],
    index_histories: dict[str, pd.DataFrame],
    names: dict[str, str],
    start: date,
    end: date,
    config: StrategyConfig = DEFAULT_STRATEGY_CONFIG,
    execution: ExecutionConfig = ExecutionConfig(),
) -> BacktestResult:
    """Replay close signals and execute each target at the next session's open."""
    if start > end:
        raise ValueError("回测开始日期不能晚于结束日期")
    execution.validate()
    if config.holdings_num != 1:
        raise ValueError("本地全仓回测仅支持holdings_num=1")

    market_dates = _common_market_dates(index_histories)
    return_dates = [item for item in market_dates if start <= item <= end]
    if not return_dates:
        raise ValueError("指定范围内没有共同的市场交易日")
    first_index = market_dates.index(return_dates[0])
    if first_index == 0:
        raise ValueError("缺少回测开始日前的交易日，无法生成初始持仓信号")

    first_signal_date = market_dates[first_index - 1]
    last_return_date = return_dates[-1]
    regime: Regime = "normal"
    regimes: dict[date, Regime] = {}
    for trading_date in market_dates:
        if trading_date > last_return_date:
            break
        slices = {
            code: frame.loc[frame["date"].dt.date <= trading_date]
            for code, frame in index_histories.items()
        }
        if any(len(frame) < config.ma_lookback for frame in slices.values()):
            continue
        market = assess_market(slices, MARKET_INDEXES, regime, config.ma_lookback)
        regime = market.regime
        regimes[trading_date] = regime

    if first_signal_date not in regimes:
        raise ValueError("回测开始日前的指数历史不足，无法判断市场状态")

    pending_targets = _targets_for_date(
        first_signal_date,
        regimes[first_signal_date],
        [],
        pool,
        histories,
        names,
        config,
    )
    records: list[BacktestDay] = []
    trades: list[BacktestTrade] = []
    cash = execution.initial_capital
    position_code: str | None = None
    position_shares = 0
    portfolio_value = execution.initial_capital
    benchmark_value = 1.0
    peak_value = 1.0
    max_drawdown = 0.0
    benchmark = index_histories["000300.XSHG"]
    previous_benchmark_close: float | None = None
    for day_index, trading_date in enumerate(return_dates):
        desired_code = pending_targets[0]
        day_trades: list[BacktestTrade] = []
        unfilled_target: str | None = None
        if desired_code != position_code:
            if position_code is not None and position_shares:
                frame = histories[position_code]
                market_price = _price_on_date(frame, trading_date, "open")
                if market_price is None:
                    raise ValueError(f"持仓标的{position_code}在{trading_date}缺少开盘价")
                trade = _build_trade(
                    trading_date,
                    "sell",
                    position_code,
                    names.get(position_code, position_code),
                    market_price,
                    position_shares,
                    execution,
                )
                cash += trade.execution_price * position_shares - execution.fixed_fee
                day_trades.append(trade)
                position_code = None
                position_shares = 0

            target_frame = histories.get(desired_code)
            if target_frame is None or target_frame.empty:
                raise ValueError(f"目标标的{desired_code}没有行情数据")
            market_price = _price_on_date(target_frame, trading_date, "open")
            if market_price is None:
                raise ValueError(f"目标标的{desired_code}在{trading_date}缺少开盘价")
            execution_price = market_price * (1 + execution.variable_cost_rate)
            affordable_lots = math.floor(
                max(0.0, cash - execution.fixed_fee)
                / (execution_price * execution.lot_size)
            )
            shares = affordable_lots * execution.lot_size
            if shares <= 0:
                unfilled_target = desired_code
            else:
                trade = _build_trade(
                    trading_date,
                    "buy",
                    desired_code,
                    names.get(desired_code, desired_code),
                    market_price,
                    shares,
                    execution,
                )
                cash -= trade.execution_price * shares + execution.fixed_fee
                day_trades.append(trade)
                position_code = desired_code
                position_shares = shares

        trades.extend(day_trades)
        held_codes = [position_code] if position_code else []
        position_frame = histories[position_code] if position_code else None
        if position_frame is None:
            current_value = cash
        else:
            current_close = _close_on_or_before(position_frame, trading_date)
            if current_close is None:
                raise ValueError(f"持仓标的{position_code}在{trading_date}缺少收盘估值")
            current_value = cash + position_shares * current_close
        daily_return = current_value / portfolio_value - 1
        portfolio_value = current_value
        net_value = portfolio_value / execution.initial_capital
        peak_value = max(peak_value, net_value)
        max_drawdown = min(max_drawdown, net_value / peak_value - 1)

        benchmark_current_close = _close_on_or_before(benchmark, trading_date)
        benchmark_base = (
            _price_on_date(benchmark, trading_date, "open")
            if day_index == 0
            else previous_benchmark_close
        )
        if (
            benchmark_base is None
            or benchmark_current_close is None
            or benchmark_base <= 0
        ):
            raise ValueError(f"沪深300在{trading_date}缺少可估值价格")
        benchmark_daily_return = benchmark_current_close / benchmark_base - 1
        benchmark_value *= 1 + benchmark_daily_return
        previous_benchmark_close = benchmark_current_close

        next_targets = _targets_for_date(
            trading_date,
            regimes[trading_date],
            held_codes,
            pool,
            histories,
            names,
            config,
        )
        if not held_codes:
            action = "现金防御"
        else:
            action = "持有" if next_targets == held_codes else "换仓"
        day_cost = sum(trade.total_cost for trade in day_trades)
        records.append(
            BacktestDay(
                date=trading_date,
                held_codes=held_codes,
                held_names=[names.get(code, code) for code in held_codes],
                daily_return=daily_return,
                cumulative_return=net_value - 1,
                benchmark_daily_return=benchmark_daily_return,
                benchmark_cumulative_return=benchmark_value - 1,
                regime=regimes[trading_date],
                action=action,
                next_targets=list(next_targets),
                portfolio_value=portfolio_value,
                cash=cash,
                position_shares=position_shares,
                trading_cost=day_cost,
                cash_defense=not held_codes,
                unfilled_target=unfilled_target,
            )
        )
        pending_targets = next_targets

    trading_days = len(records)
    annualized_return = (
        net_value ** (250 / trading_days) - 1 if trading_days and net_value > 0 else -1.0
    )
    return BacktestResult(
        requested_start=start,
        requested_end=end,
        actual_start=return_dates[0],
        actual_end=return_dates[-1],
        trading_days=trading_days,
        final_return=net_value - 1,
        final_net_value=net_value,
        annualized_return=annualized_return,
        max_drawdown=max_drawdown,
        win_rate=(
            sum(record.daily_return > 0 for record in records) / trading_days
            if trading_days
            else 0.0
        ),
        switch_count=sum(trade.side == "sell" for trade in trades),
        benchmark_final_return=benchmark_value - 1,
        initial_capital=execution.initial_capital,
        final_portfolio_value=portfolio_value,
        total_trade_cost=sum(trade.total_cost for trade in trades),
        operation_count=len(trades),
        cash_defense_days=sum(record.cash_defense for record in records),
        execution=execution,
        records=records,
        trades=trades,
    )


def write_backtest(result: BacktestResult, report_dir: Path) -> BacktestPaths:
    from mom_select.etf.backtest_reporting import render_backtest_html
    from mom_select.reporting import render_html_to_png

    report_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{result.requested_start.isoformat()}-{result.requested_end.isoformat()}-etf-backtest"
    csv_path = report_dir / f"{stem}.csv"
    trades_csv_path = report_dir / f"{stem}-trades.csv"
    json_path = report_dir / f"{stem}.json"
    html_path = report_dir / f"{stem}.html"
    image_path = report_dir / f"{stem}.png"
    rows = []
    for record in result.records:
        rows.append(
            {
                "date": record.date.isoformat(),
                "held_codes": ";".join(record.held_codes),
                "held_names": ";".join(record.held_names),
                "daily_return": record.daily_return,
                "cumulative_return": record.cumulative_return,
                "benchmark_daily_return": record.benchmark_daily_return,
                "benchmark_cumulative_return": record.benchmark_cumulative_return,
                "regime": record.regime,
                "action": record.action,
                "next_targets": ";".join(record.next_targets),
                "portfolio_value": record.portfolio_value,
                "cash": record.cash,
                "position_shares": record.position_shares,
                "trading_cost": record.trading_cost,
                "cash_defense": record.cash_defense,
                "unfilled_target": record.unfilled_target or "",
            }
        )
    pd.DataFrame(rows).to_csv(csv_path, index=False, encoding="utf-8-sig")
    pd.DataFrame(asdict(trade) for trade in result.trades).to_csv(
        trades_csv_path, index=False, encoding="utf-8-sig"
    )
    json_path.write_text(
        json.dumps(result.to_dict(), ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    html_path.write_text(render_backtest_html(result), encoding="utf-8")
    render_html_to_png(html_path, image_path)
    return BacktestPaths(
        csv=csv_path,
        trades_csv=trades_csv_path,
        json=json_path,
        html=html_path,
        image=image_path,
    )


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("日期格式必须为YYYY-MM-DD") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mom-select etf backtest", description="ETF日频轮动回测")
    parser.add_argument("--start", type=_parse_date, required=True, help="回测开始日期")
    parser.add_argument("--end", type=_parse_date, required=True, help="回测结束日期")
    parser.add_argument("--config", type=Path, help="YAML配置文件")
    parser.add_argument("--pool", type=Path, default=DEFAULT_POOL_FILE, help="ETF池CSV")
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    parser.add_argument("--offline", action="store_true", help="只使用本地缓存")
    parser.add_argument("--workers", type=int, default=8, help="行情并发请求数")
    parser.add_argument("--initial-capital", type=float, default=10_000, help="起步资金")
    parser.add_argument("--fee", type=float, default=5, help="每笔买入或卖出的固定手续费")
    parser.add_argument("--slippage-bps", type=float, default=5, help="单边滑点，单位bp")
    parser.add_argument("--impact-bps", type=float, default=2, help="单边冲击成本，单位bp")
    parser.add_argument("--premium-bps", type=float, default=10, help="单边ETF溢价折价成本，单位bp")
    parser.add_argument("--lot-size", type=int, default=100, help="每手ETF份数")
    return parser


def run(args: argparse.Namespace) -> tuple[BacktestResult, BacktestPaths]:
    config = getattr(args, "strategy_config", DEFAULT_STRATEGY_CONFIG)
    pool = load_etf_pool(args.pool)
    provider = EastmoneyDataProvider(args.cache_dir, offline=args.offline, workers=args.workers)
    warmup_start = args.start - timedelta(days=max(180, config.lookback_days * 4))
    codes = sorted(set(pool["code"]) | {config.defensive_etf})
    index_histories, index_failures = provider.histories(
        list(MARKET_INDEXES), warmup_start, args.end, full_window=True
    )
    if index_failures:
        details = "；".join(f"{code}: {reason}" for code, reason in index_failures.items())
        raise RuntimeError(f"市场指数数据不完整：{details}")
    histories, failures = provider.histories(
        codes, warmup_start, args.end, full_window=True
    )
    if config.defensive_etf in failures:
        raise RuntimeError(f"防御ETF数据获取失败：{failures[config.defensive_etf]}")
    pool_codes = set(pool["code"])
    available_count = len(pool_codes - set(failures))
    coverage = available_count / len(pool_codes) if pool_codes else 0.0
    if coverage < config.minimum_data_coverage:
        raise RuntimeError(
            f"ETF行情覆盖率仅{coverage:.1%}，低于{config.minimum_data_coverage:.0%}"
        )
    names = {code: provider.security_name(code) for code in codes}
    execution = ExecutionConfig(
        initial_capital=args.initial_capital,
        fixed_fee=args.fee,
        slippage_rate=args.slippage_bps / 10_000,
        impact_rate=args.impact_bps / 10_000,
        premium_rate=args.premium_bps / 10_000,
        lot_size=args.lot_size,
    )
    result = run_backtest(
        pool,
        histories,
        index_histories,
        names,
        args.start,
        args.end,
        config,
        execution,
    )
    return result, write_backtest(result, args.report_dir)


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.config:
        settings = load_settings(args.config)
        args.pool = settings.pool or args.pool
        args.cache_dir = settings.cache_dir or args.cache_dir
        args.report_dir = settings.report_dir or args.report_dir
        args.workers = settings.workers
        args.strategy_config = settings.strategy
    try:
        result, paths = run(args)
    except Exception as exc:
        parser.exit(1, f"回测失败：{exc}\n")
    print(f"回测区间：{result.actual_start} 至 {result.actual_end}")
    print(f"交易日数：{result.trading_days}")
    print(f"最终收益率：{result.final_return:.2%}")
    print(f"年化收益率：{result.annualized_return:.2%}")
    print(f"最大回撤：{result.max_drawdown:.2%}")
    print(f"沪深300收益率：{result.benchmark_final_return:.2%}")
    print(f"最终净值：{result.final_net_value:.6f}")
    print(f"最终资产：{result.final_portfolio_value:.2f}元")
    print(f"交易操作：{result.operation_count}笔")
    print(f"交易成本：{result.total_trade_cost:.2f}元")
    print(f"逐日结果：{paths.csv}")
    print(f"成交明细：{paths.trades_csv}")
    print(f"结构化结果：{paths.json}")
    print(f"图表报告：{paths.html}")
    print(f"图表图片：{paths.image}")


if __name__ == "__main__":
    main()
