from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path


@dataclass(frozen=True)
class StockStrategyConfig:
    trend_windows: tuple[int, ...] = (20, 60, 120)
    benchmark: str = "000300.XSHG"
    max_results: int = 20
    minimum_momentum_score: float = 0.0
    entry_candidate_results: int = 30
    minimum_universe_size: int = 1000
    minimum_listing_days: int = 250
    liquidity_lookback: int = 20
    screen_minimum_turnover: float = 300_000_000
    price_lower_bound_inclusive: float = 5.0
    price_upper_bound_inclusive: float = 55.0
    snapshot_minimum_change: float = 0.01
    snapshot_maximum_change: float = 0.10
    snapshot_minimum_turnover_rate: float = 0.03
    snapshot_maximum_turnover_rate: float = 0.15
    minimum_data_coverage: float = 0.8
    entry_max_distance_ma20: float = 0.08
    trend_minimum_return_20d: float = 0.10
    trend_minimum_return_60d: float = 0.20
    entry_max_return_20d: float = 0.20
    entry_max_drawdown_from_60d_high: float = 0.12


@dataclass(frozen=True)
class StockSecurity:
    code: str
    name: str
    board: str
    price: float
    turnover: float
    trade_status: str = "正常"
    change_pct: float = 0.0
    turnover_rate: float = 0.0


@dataclass(frozen=True)
class StockMetrics:
    code: str
    name: str
    close: float
    score: float
    return_20d: float
    return_60d: float
    return_120d: float
    relative_strength_20d: float
    relative_strength_60d: float
    r_squared: float
    atr_ratio: float
    max_drawdown: float
    average_turnover: float
    ma_aligned: bool
    distance_ma20: float
    drawdown_from_60d_high: float
    entry_score: float
    ma20_slope: float = 0.0
    ma60_slope: float = 0.0
    volume_ratio: float = 1.0
    return_5d: float = 0.0
    ma5_slope: float = 0.0
    above_ma5: bool = True
    change_pct: float = 0.0
    turnover_rate: float = 0.0
    snapshot_turnover: float = 0.0


@dataclass
class StockAdviceReport:
    generated_at: str
    as_of: date
    universe_size: int
    prefiltered_size: int
    analyzed_count: int
    rankings: list[StockMetrics]
    entry_candidates: list[StockMetrics]
    exclusion_counts: dict[str, int]
    exclusions: dict[str, str]
    failed_codes: dict[str, str]
    warnings: list[str] = field(default_factory=list)
    actionable: bool = True
    momentum_limit: int = 20
    entry_candidate_limit: int = 30

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class StockRunRequest:
    as_of: date
    cache_dir: Path
    report_dir: Path
    offline: bool = False
    workers: int = 8
    strategy: StockStrategyConfig = field(default_factory=StockStrategyConfig)
