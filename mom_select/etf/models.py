"""ETF-specific public models."""

from mom_select.models import AdviceReport, EtfMetrics, Holding, StrategyConfig

EtfAdviceReport = AdviceReport
EtfStrategyConfig = StrategyConfig

__all__ = [
    "AdviceReport",
    "EtfAdviceReport",
    "EtfMetrics",
    "EtfStrategyConfig",
    "Holding",
    "StrategyConfig",
]

