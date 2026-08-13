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


def test_prefilter_uses_strict_unadjusted_price_limit_and_keeps_bse() -> None:
    universe = pd.DataFrame(
        [
            ["600001.XSHG", "主板候选", "沪深主板", 99.99, 1e8, "正常"],
            ["600002.XSHG", "价格边界", "沪深主板", 100.0, 1e8, "正常"],
            ["300001.XSHE", "创业候选", "创业板", 20.0, 1e8, "正常"],
            ["688001.XSHG", "科创候选", "科创板", 20.0, 1e8, "正常"],
            ["600003.XSHG", "*ST样例", "沪深主板", 20.0, 1e8, "正常"],
            ["830001.XBSE", "北交候选", "北交所", 20.0, 1e8, "正常"],
        ],
        columns=["code", "name", "board", "price", "turnover", "trade_status"],
    )

    selected, exclusions, counts = prefilter_stock_universe(
        universe,
        StockStrategyConfig(minimum_universe_size=1, snapshot_minimum_turnover=0),
    )

    assert [item.code for item in selected] == ["600001.XSHG", "830001.XBSE"]
    assert exclusions["600002.XSHG"] == "股价大于等于100元"
    assert counts == {
        "股价大于等于100元": 1,
        "创业板": 1,
        "科创板": 1,
        "ST或退市风险警示": 1,
    }
