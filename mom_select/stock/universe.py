from __future__ import annotations

from collections import Counter

import pandas as pd

from mom_select.stock.models import StockSecurity, StockStrategyConfig


REQUIRED_UNIVERSE_COLUMNS = {
    "code",
    "name",
    "board",
    "price",
    "change_pct",
    "turnover",
    "turnover_rate",
    "trade_status",
}


def is_growth_board(code: str, board: str = "") -> bool:
    raw = code.split(".", 1)[0]
    normalized_board = str(board).strip().lower()
    return raw.startswith(("300", "301")) or "创业板" in normalized_board


def is_star_board(code: str, board: str = "") -> bool:
    raw = code.split(".", 1)[0]
    normalized_board = str(board).strip().lower()
    return raw.startswith(("688", "689")) or "科创板" in normalized_board


def is_risk_warning(name: str) -> bool:
    normalized = str(name).upper().replace(" ", "")
    return "ST" in normalized or "退" in normalized


def prefilter_stock_universe(
    universe: pd.DataFrame,
    config: StockStrategyConfig,
) -> tuple[list[StockSecurity], dict[str, str], dict[str, int]]:
    missing = REQUIRED_UNIVERSE_COLUMNS - set(universe.columns)
    if missing:
        raise ValueError(f"全市场股票清单缺少字段: {', '.join(sorted(missing))}")

    selected: list[StockSecurity] = []
    exclusions: dict[str, str] = {}
    counts: Counter[str] = Counter()
    for row in universe.to_dict("records"):
        code = str(row["code"])
        name = str(row["name"])
        board = str(row.get("board") or "")
        price = pd.to_numeric(row.get("price"), errors="coerce")
        change_pct = pd.to_numeric(row.get("change_pct"), errors="coerce")
        turnover = pd.to_numeric(row.get("turnover"), errors="coerce")
        turnover_rate = pd.to_numeric(row.get("turnover_rate"), errors="coerce")
        trade_status = str(row.get("trade_status") or "")
        reason = ""
        if is_growth_board(code, board):
            reason = "创业板"
        elif is_star_board(code, board):
            reason = "科创板"
        elif is_risk_warning(name):
            reason = "ST或退市风险警示"
        elif pd.isna(price):
            reason = "最新未复权价格缺失"
        elif float(price) < config.price_lower_bound_inclusive:
            reason = f"股价低于{config.price_lower_bound_inclusive:g}元"
        elif float(price) > config.price_upper_bound_inclusive:
            reason = f"股价高于{config.price_upper_bound_inclusive:g}元"
        elif pd.isna(change_pct):
            reason = "今日涨幅缺失"
        elif float(change_pct) < config.snapshot_minimum_change:
            reason = f"今日涨幅低于{config.snapshot_minimum_change:.0%}"
        elif float(change_pct) > config.snapshot_maximum_change:
            reason = f"今日涨幅高于{config.snapshot_maximum_change:.0%}"
        elif pd.isna(turnover) or float(turnover) < config.screen_minimum_turnover:
            reason = "当日成交额不足"
        elif pd.isna(turnover_rate):
            reason = "换手率缺失"
        elif float(turnover_rate) < config.snapshot_minimum_turnover_rate:
            reason = f"换手率低于{config.snapshot_minimum_turnover_rate:.0%}"
        elif float(turnover_rate) > config.snapshot_maximum_turnover_rate:
            reason = f"换手率高于{config.snapshot_maximum_turnover_rate:.0%}"
        elif trade_status and trade_status not in {"正常", "交易", "-", "None", "nan"}:
            reason = f"交易状态异常:{trade_status}"
        if reason:
            exclusions[code] = reason
            counts[reason] += 1
            continue
        selected.append(
            StockSecurity(
                code=code,
                name=name,
                board=board,
                price=float(price),
                turnover=0.0 if pd.isna(turnover) else float(turnover),
                trade_status=trade_status or "正常",
                change_pct=float(change_pct),
                turnover_rate=float(turnover_rate),
            )
        )
    return selected, exclusions, dict(counts)
