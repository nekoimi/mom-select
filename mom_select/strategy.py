from __future__ import annotations

import json
import math
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from mom_select.models import (
    AdviceReport,
    EtfMetrics,
    Holding,
    IndexSignal,
    MarketAssessment,
    Regime,
    RunMode,
    StrategyConfig,
)


def load_previous_regime(path: Path) -> Regime:
    if not path.exists():
        return "normal"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return "weak" if payload.get("regime") == "weak" else "normal"
    except (OSError, json.JSONDecodeError):
        return "normal"


def save_regime(path: Path, assessment: MarketAssessment) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"regime": assessment.regime, "as_of": assessment.as_of.isoformat()}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def assess_market(
    histories: dict[str, pd.DataFrame],
    names: dict[str, str],
    previous_regime: Regime,
    ma_lookback: int,
) -> MarketAssessment:
    signals: list[IndexSignal] = []
    for code, frame in histories.items():
        if len(frame) < ma_lookback:
            continue
        recent = frame.sort_values("date").iloc[-ma_lookback:]
        close = float(recent["close"].iloc[-1])
        moving_average = float(recent["close"].mean())
        relation = "above" if close > moving_average else "below" if close < moving_average else "equal"
        signals.append(
            IndexSignal(
                code=code,
                name=names.get(code, code),
                close=close,
                moving_average=moving_average,
                relation=relation,
            )
        )
    if len(signals) < 4:
        raise ValueError(f"市场判断所需4个指数仅有{len(signals)}个数据完整")
    above_count = sum(signal.relation == "above" for signal in signals)
    below_count = sum(signal.relation == "below" for signal in signals)
    regime: Regime = previous_regime
    if previous_regime == "normal" and below_count >= 3:
        regime = "weak"
        explanation = f"{below_count}/4个指数低于MA{ma_lookback}，由正常期切换为走弱期"
    elif previous_regime == "weak" and above_count >= 3:
        regime = "normal"
        explanation = f"{above_count}/4个指数高于MA{ma_lookback}，由走弱期切换为正常期"
    else:
        explanation = f"未满足状态切换条件，维持{'走弱期' if regime == 'weak' else '正常期'}"
    as_of = max(frame["date"].max().date() for frame in histories.values() if not frame.empty)
    return MarketAssessment(
        regime=regime,
        previous_regime=previous_regime,
        as_of=as_of,
        above_count=above_count,
        below_count=below_count,
        signals=signals,
        explanation=explanation,
    )


def calculate_momentum(prices: np.ndarray, lookback_days: int) -> tuple[float, float, float]:
    if len(prices) < lookback_days + 1 or np.any(prices <= 0):
        raise ValueError("动量计算价格数据不足或包含非正数")
    recent = prices[-(lookback_days + 1) :]
    y_values = np.log(recent)
    x_values = np.arange(len(y_values), dtype=float)
    weights = np.linspace(1.0, 2.0, len(y_values)) ** 2
    weight_sum = weights.sum()
    x_mean = np.sum(weights * x_values) / weight_sum
    y_mean = np.sum(weights * y_values) / weight_sum
    x_delta = x_values - x_mean
    y_delta = y_values - y_mean
    variance_x = np.sum(weights * x_delta**2)
    if variance_x == 0:
        return 0.0, 0.0, 0.0
    slope = np.sum(weights * x_delta * y_delta) / variance_x
    intercept = y_mean - slope * x_mean
    annualized_return = math.exp(slope * 250) - 1
    predicted = slope * x_values + intercept
    base_weights = np.sqrt(weights)
    residual = np.sum(base_weights * (y_values - predicted) ** 2)
    total = np.sum(base_weights * (y_values - np.mean(y_values)) ** 2)
    r_squared = 1 - residual / total if total else 0.0
    return annualized_return * r_squared, annualized_return, r_squared


def calculate_metrics(
    code: str,
    name: str,
    frame: pd.DataFrame,
    regime: Regime,
    config: StrategyConfig,
    intraday_volume_multiplier: float = 1.0,
) -> EtfMetrics:
    required_rows = max(
        config.lookback_days + 1,
        config.ma_lookback,
        config.volume_lookback + 1,
        config.liquidity_lookback,
        4,
    )
    clean = frame.sort_values("date").dropna(subset=["close", "volume", "turnover"])
    if len(clean) < required_rows:
        raise ValueError(f"仅有{len(clean)}行有效数据，至少需要{required_rows}行")
    closes = clean["close"].to_numpy(dtype=float)
    score, annualized_return, r_squared = calculate_momentum(closes, config.lookback_days)
    close = float(closes[-1])
    moving_average = float(np.mean(closes[-config.ma_lookback :]))
    prior_volumes = clean["volume"].to_numpy(dtype=float)[-(config.volume_lookback + 1) : -1]
    projected_volume = float(clean["volume"].iloc[-1]) * intraday_volume_multiplier
    volume_ratio = float(projected_volume / np.mean(prior_volumes))
    if intraday_volume_multiplier > 1:
        turnover_window = clean["turnover"].iloc[-(config.liquidity_lookback + 1) : -1]
    else:
        turnover_window = clean["turnover"].iloc[-config.liquidity_lookback :]
    average_turnover = float(turnover_window.mean())
    daily_ratios = closes[-3:] / closes[-4:-1]
    regime_filter = r_squared > config.r2_threshold if regime == "normal" else close > moving_average
    return EtfMetrics(
        code=code,
        name=name,
        momentum_score=score,
        annualized_return=annualized_return,
        r_squared=r_squared,
        close=close,
        moving_average=moving_average,
        volume_ratio=volume_ratio,
        average_turnover=average_turnover,
        passed_momentum=config.min_score <= score <= config.max_score,
        passed_regime_filter=regime_filter,
        passed_volume=volume_ratio < config.volume_threshold,
        passed_loss=bool(np.min(daily_ratios) >= config.daily_loss_floor),
        passed_liquidity=average_turnover >= config.min_average_turnover,
    )


def choose_targets(
    eligible: list[EtfMetrics],
    holdings: list[Holding],
    regime: Regime,
    config: StrategyConfig,
) -> tuple[list[EtfMetrics], list[str]]:
    top = eligible[:10]
    if not top:
        return [], [config.defensive_etf]
    reference_index = min(config.holdings_num, len(top)) - 1
    reference_score = top[reference_index].momentum_score
    ratio = config.retention_ratio if regime == "normal" else 1.0
    threshold = reference_score * ratio
    candidates = [item for item in top if item.momentum_score >= threshold]
    candidate_by_code = {item.code: item for item in candidates}
    retained = [candidate_by_code[item.code] for item in holdings if item.code in candidate_by_code]
    retained.sort(key=lambda item: item.momentum_score, reverse=True)
    targets = retained[: config.holdings_num]
    if len(targets) < config.holdings_num:
        target_codes = {item.code for item in targets}
        targets.extend(
            item
            for item in candidates
            if item.code not in target_codes
        )
    targets = targets[: config.holdings_num]
    return candidates, [item.code for item in targets]


def build_report(
    as_of: date,
    market: MarketAssessment,
    pool_size: int,
    rankings: list[EtfMetrics],
    failures: dict[str, str],
    holdings: list[Holding],
    config: StrategyConfig,
    provider_warnings: list[str] | None = None,
    run_mode: RunMode = "close",
    signal_time: str | None = None,
    debug: bool = False,
    historical_simulation: bool = False,
    discovered_pool_size: int = 0,
    fixed_pool_size: int = 0,
    dynamic_pool_size: int = 0,
) -> AdviceReport:
    rankings.sort(key=lambda item: item.momentum_score, reverse=True)
    eligible = [item for item in rankings if item.passed_all]
    candidates, targets = choose_targets(eligible, holdings, market.regime, config)
    current_codes = [holding.code for holding in holdings if holding.amount > 0]
    coverage = len(rankings) / pool_size if pool_size else 0
    warnings: list[str] = list(provider_warnings or [])
    if historical_simulation:
        warnings.append("历史DEBUG为重建信号，仅用于策略对照，不作为实盘信号")
    elif run_mode == "intraday":
        warnings.append("13:05信号基于盘中快照，收盘前价格和排名仍可能变化")
    if debug:
        warnings.append("DEBUG模式：策略时钟固定为13:05，结果仅用于本地调试，不可作为实盘信号")
    data_actionable = coverage >= config.minimum_data_coverage
    if not data_actionable:
        warnings.append(
            f"数据覆盖率仅{coverage:.1%}，低于{config.minimum_data_coverage:.0%}，本次结果不可执行"
        )
    if failures:
        warnings.append(f"{len(failures)}只ETF数据获取或计算失败")
    if not eligible:
        warnings.append("没有风险ETF通过全部过滤，策略目标为防御ETF")
    if not data_actionable:
        action = "等待"
        explanation = "关键数据覆盖不足，请修复数据后重新运行"
        targets = []
    elif current_codes == targets:
        action = "持有"
        explanation = "当前持仓与策略目标一致"
    elif not current_codes:
        action = "买入候选"
        explanation = "当前未录入持仓，可人工核查后建立目标仓位"
    else:
        action = "换仓"
        explanation = "当前持仓不在最终目标中；先确认卖出成交，再考虑买入目标"
    actionable = data_actionable and not debug
    if debug:
        action = f"调试：{action}"
        explanation = f"仅用于本地流程检查；{explanation}"
    return AdviceReport(
        generated_at=datetime.now().astimezone().isoformat(timespec="seconds"),
        as_of=as_of,
        market=market,
        pool_size=pool_size,
        analyzed_count=len(rankings),
        failed_codes=sorted(failures),
        rankings=rankings,
        eligible=eligible[:10],
        candidates=candidates,
        current_holdings=holdings,
        targets=targets,
        action=action,
        explanation=explanation,
        discovered_pool_size=discovered_pool_size,
        fixed_pool_size=fixed_pool_size,
        dynamic_pool_size=dynamic_pool_size,
        run_mode=run_mode,
        signal_time=signal_time,
        debug=debug,
        historical_simulation=historical_simulation,
        warnings=warnings,
        actionable=actionable,
    )
