from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from mom_select.models import StrategyConfig


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
class AppSettings:
    project_dir: Path = Path(".")
    pool: Path | None = None
    portfolio: Path | None = None
    cache_dir: Path | None = None
    report_dir: Path | None = None
    state_file: Path | None = None
    workers: int = 8
    fixed_pool_only: bool = False
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    notification: NotificationSettings = field(default_factory=NotificationSettings)
    schedule: ScheduleSettings = field(default_factory=ScheduleSettings)


def load_settings(path: Path) -> AppSettings:
    raw = _expand(yaml.safe_load(path.read_text(encoding="utf-8")) or {})
    runtime = raw.get("runtime", {})
    paths = raw.get("paths", {})
    notification = raw.get("notifications", {})
    schedule = raw.get("schedule", {})
    strategy_raw = raw.get("strategy", {})
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
    settings = AppSettings(
        project_dir=project_dir,
        pool=resolve(paths.get("pool")),
        portfolio=resolve(paths.get("portfolio")),
        cache_dir=resolve(paths.get("cache_dir")),
        report_dir=resolve(paths.get("report_dir")),
        state_file=resolve(paths.get("state_file")),
        workers=int(runtime.get("workers", 8)),
        fixed_pool_only=bool(runtime.get("fixed_pool_only", False)),
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
    )
    return settings
