from pathlib import Path

from mom_select.models import StrategyConfig


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_POOL_FILE = PROJECT_ROOT / "config" / "etf_pool.csv"
DEFAULT_CACHE_DIR = PROJECT_ROOT / "data" / "cache"
DEFAULT_STATE_FILE = PROJECT_ROOT / "data" / "market_state.json"
DEFAULT_REPORT_DIR = PROJECT_ROOT / "reports" / "etf"

MARKET_INDEXES = {
    "000300.XSHG": "沪深300",
    "399101.XSHE": "深证综指/小盘",
    "399006.XSHE": "创业板指",
    "000510.XSHG": "中证A500",
}

DEFAULT_STRATEGY_CONFIG = StrategyConfig()
