from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo


SHANGHAI = ZoneInfo("Asia/Shanghai")


def report_generated_at() -> str:
    return datetime.now(SHANGHAI).isoformat(timespec="seconds")


def format_report_time(value: str) -> str:
    try:
        generated_at = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    if generated_at.tzinfo is None:
        generated_at = generated_at.replace(tzinfo=SHANGHAI)
    else:
        generated_at = generated_at.astimezone(SHANGHAI)
    return generated_at.strftime("%Y年%m月%d日 %H:%M:%S（北京时间）")
