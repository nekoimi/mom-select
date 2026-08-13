"""ETF strategy API retained behind a business-specific namespace."""

from mom_select.strategy import (
    assess_market,
    build_report,
    calculate_metrics,
    choose_targets,
    load_previous_regime,
    save_regime,
)

__all__ = [
    "assess_market",
    "build_report",
    "calculate_metrics",
    "choose_targets",
    "load_previous_regime",
    "save_regime",
]

