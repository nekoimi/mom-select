from pathlib import Path

import pandas as pd

from mom_select.models import Holding


def load_holdings(path: Path | None) -> list[Holding]:
    if path is None or not path.exists():
        return []
    frame = pd.read_csv(path, dtype={"code": str})
    if "code" not in frame.columns:
        raise ValueError("持仓文件至少需要code字段")
    holdings = []
    for row in frame.to_dict("records"):
        holdings.append(
            Holding(
                code=row["code"],
                name=str(row.get("name", "") or ""),
                amount=float(row.get("amount", 0) or 0),
                avg_cost=float(row.get("avg_cost", 0) or 0),
            )
        )
    return [holding for holding in holdings if holding.amount > 0]
