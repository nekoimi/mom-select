from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from mom_select.models import StrategyConfig
from mom_select.stock.models import StockStrategyConfig


_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _expand(value: Any) -> Any:
    if isinstance(value, str):
        return _ENV_PATTERN.sub(lambda match: os.getenv(match.group(1), ""), value)
    if isinstance(value, dict):
        return {key: _expand(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_expand(item) for item in value]
    return value


@dataclass(frozen=True)
class NotificationSettings:
    enabled: bool = False
    channels: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class ScheduleSettings:
    timezone: str = "Asia/Shanghai"
    day_of_week: str = "mon-fri"
    hour: int = 13
    minute: int = 5
    misfire_grace_time: int = 300


@dataclass(frozen=True)
class StockTaskSettings:
    enabled: bool = False
    cache_dir: Path = Path("data/cache/stock")
    report_dir: Path = Path("reports/stock")
    strategy: StockStrategyConfig = field(default_factory=StockStrategyConfig)
    schedule: ScheduleSettings = field(
        default_factory=lambda: ScheduleSettings(hour=15, minute=20)
    )


@dataclass(frozen=True)
class AppSettings:
    project_dir: Path = Path(".")
    pool: Path | None = None
    portfolio: Path | None = None
    cache_dir: Path | None = None
    report_dir: Path | None = None
    state_file: Path | None = None
    workers: int = 8
    fixed_pool_only: bool = False
    etf_enabled: bool = True
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    notification: NotificationSettings = field(default_factory=NotificationSettings)
    schedule: ScheduleSettings = field(default_factory=ScheduleSettings)
    stock: StockTaskSettings = field(default_factory=StockTaskSettings)


def load_settings(path: Path) -> AppSettings:
    raw = _expand(yaml.safe_load(path.read_text(encoding="utf-8")) or {})
    runtime = raw.get("runtime", {})
    paths = raw.get("paths", {})
    notification = raw.get("notifications", {})
    schedule = raw.get("schedule", {})
    strategy_raw = raw.get("strategy", {})
    tasks = raw.get("tasks", {})
    etf_task = tasks.get("etf", {})
    stock_task = tasks.get("stock", {})
    if etf_task:
        schedule = etf_task.get("schedule", schedule)
        strategy_raw = etf_task.get("strategy", strategy_raw)
        paths = {**paths, **etf_task.get("paths", {})}
    config_base = path.parent.parent if path.parent.name == "config" else path.parent

    project_value = runtime.get("project_dir")
    if project_value:
        project_path = Path(str(project_value)).expanduser()
        project_dir = project_path if project_path.is_absolute() else config_base / project_path
    else:
        project_dir = config_base

    def resolve(value: str | None) -> Path | None:
        if not value:
            return None
        item = Path(str(value)).expanduser()
        return item if item.is_absolute() else project_dir / item

    strategy = StrategyConfig(
        **{
            field_name: strategy_raw.get(field_name, getattr(StrategyConfig(), field_name))
            for field_name in StrategyConfig.__dataclass_fields__
        }
    )
    if int(runtime.get("workers", 8)) < 1:
        raise ValueError("runtime.workers必须为正整数")
    positive_fields = ("lookback_days", "ma_lookback", "volume_lookback", "liquidity_lookback", "holdings_num")
    if any(getattr(strategy, field_name) < 1 for field_name in positive_fields):
        raise ValueError("策略窗口和持仓数量必须为正整数")
    if not 0 <= strategy.minimum_data_coverage <= 1:
        raise ValueError("strategy.minimum_data_coverage必须在0到1之间")
    if not 0 <= strategy.retention_ratio <= 1:
        raise ValueError("strategy.retention_ratio必须在0到1之间")
    if strategy.min_score > strategy.max_score:
        raise ValueError("strategy.min_score不能大于max_score")
    stock_strategy_raw = stock_task.get("strategy", {})
    stock_defaults = StockStrategyConfig()
    stock_strategy_values = {
        field_name: stock_strategy_raw.get(field_name, getattr(stock_defaults, field_name))
        for field_name in StockStrategyConfig.__dataclass_fields__
    }
    stock_strategy_values["trend_windows"] = tuple(stock_strategy_values["trend_windows"])
    stock_strategy = StockStrategyConfig(**stock_strategy_values)
    if not stock_strategy.trend_windows or any(window < 1 for window in stock_strategy.trend_windows):
        raise ValueError("tasks.stock.strategy.trend_windows必须为正整数")
    if (
        stock_strategy.max_results < 1
        or stock_strategy.entry_candidate_results < 1
        or stock_strategy.minimum_universe_size < 1
        or stock_strategy.minimum_listing_days < 1
    ):
        raise ValueError("个股排名数量和上市时间门槛必须为正整数")
    if stock_strategy.price_upper_bound_exclusive <= 0:
        raise ValueError("个股价格上限必须为正数")
    if stock_strategy.snapshot_minimum_turnover < 0:
        raise ValueError("个股快照成交额门槛不能为负数")
    if (
        stock_strategy.entry_max_distance_ma20 <= 0
        or stock_strategy.entry_max_return_20d <= 0
        or stock_strategy.entry_max_atr_ratio <= 0
        or stock_strategy.entry_max_drawdown_from_60d_high <= 0
    ):
        raise ValueError("个股介入候选阈值必须为正数")
    if stock_strategy.entry_min_return_20d > stock_strategy.entry_max_return_20d:
        raise ValueError("个股介入候选20日收益下限不能高于上限")
    if not 0 <= stock_strategy.entry_min_r_squared <= 1:
        raise ValueError("个股介入候选R²门槛必须在0到1之间")
    if not 0 <= stock_strategy.minimum_data_coverage <= 1:
        raise ValueError("个股最低数据覆盖率必须在0到1之间")
    stock_paths = stock_task.get("paths", {})
    stock_schedule_raw = stock_task.get("schedule", {})
    settings = AppSettings(
        project_dir=project_dir,
        pool=resolve(paths.get("pool")),
        portfolio=resolve(paths.get("portfolio")),
        cache_dir=resolve(paths.get("cache_dir")),
        report_dir=resolve(paths.get("report_dir")),
        state_file=resolve(paths.get("state_file")),
        workers=int(runtime.get("workers", 8)),
        fixed_pool_only=bool(runtime.get("fixed_pool_only", False)),
        etf_enabled=bool(etf_task.get("enabled", True)),
        strategy=strategy,
        notification=NotificationSettings(
            enabled=bool(notification.get("enabled", False)),
            channels=list(notification.get("channels", [])),
        ),
        schedule=ScheduleSettings(
            timezone=str(schedule.get("timezone", "Asia/Shanghai")),
            day_of_week=str(schedule.get("day_of_week", "mon-fri")),
            hour=int(schedule.get("hour", 13)),
            minute=int(schedule.get("minute", 5)),
            misfire_grace_time=int(schedule.get("misfire_grace_time", 300)),
        ),
        stock=StockTaskSettings(
            enabled=bool(stock_task.get("enabled", False)),
            cache_dir=resolve(stock_paths.get("cache_dir")) or project_dir / "data/cache/stock",
            report_dir=resolve(stock_paths.get("report_dir")) or project_dir / "reports/stock",
            strategy=stock_strategy,
            schedule=ScheduleSettings(
                timezone=str(stock_schedule_raw.get("timezone", "Asia/Shanghai")),
                day_of_week=str(stock_schedule_raw.get("day_of_week", "mon-fri")),
                hour=int(stock_schedule_raw.get("hour", 15)),
                minute=int(stock_schedule_raw.get("minute", 20)),
                misfire_grace_time=int(stock_schedule_raw.get("misfire_grace_time", 300)),
            ),
        ),
    )
    return settings
