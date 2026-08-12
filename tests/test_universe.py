import pandas as pd

from mom_select.universe import select_dynamic_etfs


def test_select_dynamic_etfs_keeps_most_liquid_member_per_cleaned_group() -> None:
    universe = pd.DataFrame(
        {
            "code": [
                "159768.XSHE",
                "159707.XSHE",
                "159698.XSHE",
                "510300.XSHG",
                "511010.XSHG",
            ],
            "name": ["房地产ETF银华", "房地产ETF华宝", "粮食ETF鹏华", "沪深300ETF", "国债ETF"],
        }
    )
    turnover = pd.Series(
        {
            "159768.XSHE": 30_000_000,
            "159707.XSHE": 20_000_000,
            "159698.XSHE": 25_000_000,
            "510300.XSHG": 100_000_000,
            "511010.XSHG": 100_000_000,
        }
    )

    result = select_dynamic_etfs(universe, turnover, threshold=10_000_000)

    assert "159768.XSHE" in result
    assert "159707.XSHE" not in result
    assert "159698.XSHE" in result
    assert "510300.XSHG" not in result
    assert "511010.XSHG" not in result
