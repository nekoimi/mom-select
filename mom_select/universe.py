from __future__ import annotations

from pathlib import Path
from dataclasses import dataclass

import pandas as pd


FUND_COMPANIES = sorted(
    {
        "易方达", "广发", "华夏", "华安", "嘉实", "富国", "招商", "鹏华", "南方",
        "汇添富", "国泰", "平安", "银华", "天弘", "建信", "工银", "华泰柏瑞",
        "博时", "景顺长城", "景顺", "华宝", "申万菱信", "万家", "中欧", "兴证全球",
        "浙商", "诺安", "前海开源", "泰康", "泰达宏利", "农银汇理", "交银",
        "东方红", "财通", "华商", "国联", "永赢", "金鹰", "德邦", "创金合信",
        "西部利得", "圆信永丰", "泓德", "汇安", "诺德", "恒生前海", "华润元大",
        "大成", "海富通", "摩根", "华泰", "中信", "中银", "兴全", "国信", "长城",
        "中金", "浙商证券", "东海", "东吴", "浦银安盛", "信达澳亚", "中加", "中航",
        "中融", "中邮", "中庚", "中信保诚", "中信建投", "中银国际", "中银证券",
        "九泰", "交银施罗德", "光大保德信", "兴银", "农银", "国投瑞银",
        "国海富兰克林", "国联安", "国金", "太平", "方正富邦", "民生加银", "汇丰晋信",
        "银河", "长信", "长安", "长盛", "长江证券", "鹏扬",
    },
    key=len,
    reverse=True,
)

NOISE_WORDS = sorted(
    {
        "6666", "8888", "9999", "A类", "AH", "B", "BS", "C", "C类", "CS", "DB",
        "E", "E类", "ETF基金", "ETF联接", "ETF", "FG", "G60", "GF", "GT", "HGS",
        "LOF基金", "LOF联接", "LOF", "SG", "SZ", "TF", "TK", "WJ", "YH", "ZS",
        "ZZ", "板块", "策略", "产业", "场内", "场外", "低波", "基本面", "基金", "精选",
        "联接基金", "联接", "量化", "龙头", "民企", "民营", "国企", "央企", "智能",
        "全指", "上市开放式", "指基", "指增", "指数ETF", "指数基金", "指数A", "指数C",
        "指数", "主题", "增强", "上海", "四川", "浙江", "湖北", "2000", "1000", "500",
        "300", "100", "50", "30", "黄", "大", "新",
    },
    key=len,
    reverse=True,
)

SPECIAL_GROUPS = (
    (
        "香港组",
        ("港股通", "恒生", "恒指", "香港", "中概", "HS科技", "H股", "HKC", "HGS", "港股", "HK", "港", "H"),
    ),
    ("科创组", ("科创创业", "科创板", "科创", "科综", "双创", "创创", "K C", "KC")),
    ("创业组", ("创业板", "创成长", "创业", "创板")),
    ("美指组", ("纳斯达克", "标普", "纳指")),
)

EXCLUDE_KEYWORDS = sorted(
    {
        "2000", "1000", "800", "500", "300", "200", "180", "100", "50", "30",
        "沪深", "中证", "上证", "深证", "深成", "A50", "A100", "A500", "深100",
        "短融", "可转债", "转债", "双债", "利率债", "国债", "地债", "政金债", "国开债",
        "基准国债", "新综债", "信用债", "企业债", "公司债", "城投债", "城投", "美元债",
        "沪公司债", "科创债", "科债", "科创AAA", "自由现金流", "现金流", "现金流E",
        "现金流基", "现金流TF", "现金流全", "300现金流", "800现金流", "货币", "快线",
        "快钱", "中银现金", "500现金", "800现金", "现金800", "现金自由", "现金指数",
        "全指现金", "现金全指", "ESG", "MSCI", "MS", "现金", "债",
    },
    key=len,
    reverse=True,
)


@dataclass(frozen=True)
class UniverseSelection:
    codes: list[str]
    fixed_codes: list[str]
    dynamic_codes: list[str]
    discovered_count: int
    liquidity_threshold: float
    history_failures: int


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


def _special_group(name: str) -> tuple[str, tuple[str, ...]] | None:
    for group_name, keywords in SPECIAL_GROUPS:
        if any(keyword in name for keyword in keywords):
            return group_name, keywords
    return None


def _clean_name(name: str, special: tuple[str, tuple[str, ...]] | None) -> str:
    cleaned = name
    for company in FUND_COMPANIES:
        cleaned = cleaned.replace(company, "")
    if special:
        for keyword in special[1]:
            cleaned = cleaned.replace(keyword, "")
    for word in NOISE_WORDS:
        cleaned = cleaned.replace(word, "")
    return cleaned.strip()


def dynamic_universe_candidates(universe: pd.DataFrame) -> pd.DataFrame:
    keep = []
    for row in universe.itertuples(index=False):
        name = str(row.name)
        if any(keyword in name for keyword in EXCLUDE_KEYWORDS):
            continue
        special = _special_group(name)
        if not _clean_name(name, special):
            continue
        keep.append({"code": str(row.code), "name": name})
    return pd.DataFrame(keep, columns=["code", "name"]).drop_duplicates("code")


def select_dynamic_etfs(
    universe: pd.DataFrame,
    average_turnover: pd.Series,
    threshold: float,
    limit: int = 300,
) -> list[str]:
    grouped: dict[str, tuple[str, float]] = {}
    for row in universe.itertuples(index=False):
        code = str(row.code)
        name = str(row.name)
        if any(keyword in name for keyword in EXCLUDE_KEYWORDS):
            continue
        money = float(average_turnover.get(code, 0.0))
        if money <= threshold:
            continue
        special = _special_group(name)
        cleaned = _clean_name(name, special)
        if not cleaned:
            continue
        industry = cleaned[:2]
        group_key = f"{special[0]}_{industry}" if special else industry
        current = grouped.get(group_key)
        if current is None or money > current[1]:
            grouped[group_key] = (code, money)
    return [
        code
        for code, _ in sorted(grouped.values(), key=lambda item: item[1], reverse=True)[:limit]
    ]


def build_normal_universe(
    fixed_pool: pd.DataFrame,
    discovered: pd.DataFrame,
    histories: dict[str, pd.DataFrame],
    history_failures: int = 0,
    threshold_divisor: float = 20_000,
    fallback_threshold: float = 10_000_000,
) -> UniverseSelection:
    daily_totals: dict[object, float] = {}
    average_turnover: dict[str, float] = {}
    for code, frame in histories.items():
        if frame is None or frame.empty:
            continue
        recent = frame.sort_values("date").tail(3)
        if len(recent) < 3:
            continue
        average_turnover[str(code)] = float(recent["turnover"].sum() / 3)
    for row in discovered.itertuples(index=False):
        frame = histories.get(str(row.code))
        if frame is None or frame.empty:
            continue
        recent = frame.sort_values("date").tail(3)
        if len(recent) < 3:
            continue
        for item in recent.itertuples(index=False):
            day = item.date.date()
            daily_totals[day] = daily_totals.get(day, 0.0) + float(item.turnover)
    last_totals = [daily_totals[key] for key in sorted(daily_totals)[-3:]]
    threshold = (
        sum(last_totals) / len(last_totals) / threshold_divisor
        if len(last_totals) == 3
        else fallback_threshold
    )
    money = pd.Series(average_turnover, dtype=float)
    dynamic_codes = select_dynamic_etfs(discovered, money, threshold)
    fixed_codes = [
        code
        for code in fixed_pool["code"].tolist()
        if float(money.get(code, 0.0)) > threshold
    ]
    if not fixed_codes:
        fixed_codes = fixed_pool["code"].tolist()
    codes = sorted(set(fixed_codes + dynamic_codes))
    return UniverseSelection(
        codes=codes,
        fixed_codes=fixed_codes,
        dynamic_codes=dynamic_codes,
        discovered_count=len(discovered),
        liquidity_threshold=threshold,
        history_failures=history_failures,
    )
