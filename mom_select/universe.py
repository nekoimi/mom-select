from __future__ import annotations

from pathlib import Path

import pandas as pd


def load_etf_pool(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path, dtype={"code": str, "bucket": str})
    required = {"code", "bucket"}
    if not required.issubset(frame.columns):
        raise ValueError(f"ETF池缺少字段: {sorted(required - set(frame.columns))}")
    if frame["code"].duplicated().any():
        duplicates = frame.loc[frame["code"].duplicated(), "code"].tolist()
        raise ValueError(f"ETF池存在重复代码: {duplicates}")
    invalid = frame.loc[~frame["bucket"].isin(["global", "china"]), "bucket"].unique()
    if len(invalid):
        raise ValueError(f"ETF池包含未知分类: {invalid.tolist()}")
    return frame
