import pandas as pd

from mom_select.stock.models import StockStrategyConfig
from mom_select.stock.universe import (
    is_growth_board,
    is_risk_warning,
    is_star_board,
    prefilter_stock_universe,
)


def test_board_and_risk_warning_rules() -> None:
    assert is_growth_board("300001.XSHE")
    assert is_growth_board("301001.XSHE")
    assert is_growth_board("000001.XSHE", "创业板")
    assert is_star_board("688001.XSHG")
    assert is_star_board("689001.XSHG")
    assert is_risk_warning("*ST示例")
    assert is_risk_warning("st示例")
    assert not is_risk_warning("中视传媒")


def test_prefilter_applies_tonghuashun_snapshot_rules_and_keeps_bse() -> None:
    universe = pd.DataFrame(
        [
            ["600001.XSHG", "下边界", "沪深主板", 5.0, 0.01, 3e8, 0.03, "正常"],
            ["600002.XSHG", "上边界", "沪深主板", 55.0, 0.10, 4e8, 0.15, "正常"],
            ["600003.XSHG", "价格过低", "沪深主板", 4.99, 0.03, 4e8, 0.05, "正常"],
            ["600004.XSHG", "价格过高", "沪深主板", 55.01, 0.03, 4e8, 0.05, "正常"],
            ["600005.XSHG", "涨幅过低", "沪深主板", 20.0, 0.009, 4e8, 0.05, "正常"],
            ["600006.XSHG", "涨幅过高", "沪深主板", 20.0, 0.101, 4e8, 0.05, "正常"],
            ["600007.XSHG", "成交不足", "沪深主板", 20.0, 0.03, 2.99e8, 0.05, "正常"],
            ["600008.XSHG", "换手过低", "沪深主板", 20.0, 0.03, 4e8, 0.029, "正常"],
            ["600009.XSHG", "换手过高", "沪深主板", 20.0, 0.03, 4e8, 0.151, "正常"],
            ["300001.XSHE", "创业候选", "创业板", 20.0, 0.03, 4e8, 0.05, "正常"],
            ["688001.XSHG", "科创候选", "科创板", 20.0, 0.03, 4e8, 0.05, "正常"],
            ["600010.XSHG", "*ST样例", "沪深主板", 20.0, 0.03, 4e8, 0.05, "正常"],
            ["830001.XBSE", "北交候选", "北交所", 20.0, 0.03, 4e8, 0.05, "正常"],
        ],
        columns=[
            "code", "name", "board", "price", "change_pct", "turnover",
            "turnover_rate", "trade_status",
        ],
    )

    selected, exclusions, counts = prefilter_stock_universe(
        universe, StockStrategyConfig(minimum_universe_size=1)
    )

    assert [item.code for item in selected] == [
        "600001.XSHG", "600002.XSHG", "830001.XBSE",
    ]
    assert selected[0].change_pct == 0.01
    assert selected[1].turnover_rate == 0.15
    assert exclusions["600003.XSHG"] == "股价低于5元"
    assert exclusions["600004.XSHG"] == "股价高于55元"
    assert counts == {
        "股价低于5元": 1,
        "股价高于55元": 1,
        "今日涨幅低于1%": 1,
        "今日涨幅高于10%": 1,
        "当日成交额不足": 1,
        "换手率低于3%": 1,
        "换手率高于15%": 1,
        "创业板": 1,
        "科创板": 1,
        "ST或退市风险警示": 1,
    }
