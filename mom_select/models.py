from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Literal


Regime = Literal["normal", "weak"]


@dataclass(frozen=True)
class StrategyConfig:
    lookback_days: int = 25
    min_score: float = 0.0
    max_score: float = 5.0
    r2_threshold: float = 0.4
    ma_lookback: int = 10
    volume_lookback: int = 5
    volume_threshold: float = 1.8
    daily_loss_floor: float = 0.97
    liquidity_lookback: int = 3
    min_average_turnover: float = 10_000_000
    holdings_num: int = 1
    retention_ratio: float = 0.9
    defensive_etf: str = "511880.XSHG"
    minimum_data_coverage: float = 0.8


@dataclass(frozen=True)
class IndexSignal:
    code: str
    name: str
    close: float
    moving_average: float
    relation: Literal["above", "below", "equal"]


@dataclass(frozen=True)
class MarketAssessment:
    regime: Regime
    previous_regime: Regime
    as_of: date
    above_count: int
    below_count: int
    signals: list[IndexSignal]
    explanation: str


@dataclass(frozen=True)
class EtfMetrics:
    code: str
    name: str
    momentum_score: float
    annualized_return: float
    r_squared: float
    close: float
    moving_average: float
    volume_ratio: float
    average_turnover: float
    passed_momentum: bool
    passed_regime_filter: bool
    passed_volume: bool
    passed_loss: bool
    passed_liquidity: bool

    @property
    def passed_all(self) -> bool:
        return all(
            (
                self.passed_momentum,
                self.passed_regime_filter,
                self.passed_volume,
                self.passed_loss,
                self.passed_liquidity,
            )
        )


@dataclass(frozen=True)
class Holding:
    code: str
    name: str = ""
    amount: float = 0
    avg_cost: float = 0


@dataclass
class AdviceReport:
    generated_at: str
    as_of: date
    market: MarketAssessment
    pool_size: int
    analyzed_count: int
    failed_codes: list[str]
    rankings: list[EtfMetrics]
    eligible: list[EtfMetrics]
    candidates: list[EtfMetrics]
    current_holdings: list[Holding]
    targets: list[str]
    action: str
    explanation: str
    warnings: list[str] = field(default_factory=list)
    actionable: bool = True

    def to_dict(self) -> dict:
        return asdict(self)
