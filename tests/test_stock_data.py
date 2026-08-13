from datetime import date

import pandas as pd
import pytest

from mom_select.data import DataProviderError
from mom_select.stock.data import AkshareStockDataProvider, _exchange_code, _raw_stock_code


def test_stock_exchange_code_supports_shenzhen_shanghai_and_bse() -> None:
    assert _exchange_code("000001") == "000001.XSHE"
    assert _exchange_code("600000") == "600000.XSHG"
    assert _exchange_code("830001") == "830001.XBSE"
    assert _raw_stock_code("sh600000") == "600000"
    assert _raw_stock_code("sz000001") == "000001"
    assert _raw_stock_code("bj830001") == "830001"


def test_offline_stock_universe_requires_same_day_cache(tmp_path) -> None:
    provider = AkshareStockDataProvider(tmp_path, offline=True)

    with pytest.raises(DataProviderError, match="缺少个股全市场快照缓存"):
        provider.stock_universe(date(2026, 8, 13))


def test_tencent_universe_normalizes_price_and_turnover_units() -> None:
    source = pd.DataFrame(
        [{"code": "sh600000", "name": "浦发银行", "zxj": "1250", "turnover": "321.5", "state": ""}]
    )

    result = AkshareStockDataProvider._normalize_universe(
        source, "腾讯", date(2026, 8, 13)
    )

    assert result.loc[0, "code"] == "600000.XSHG"
    assert result.loc[0, "price"] == 1250
    assert result.loc[0, "turnover"] == 3_215_000
    assert result.loc[0, "trade_status"] == "正常"


def test_stock_universe_falls_back_from_tencent_to_eastmoney(tmp_path, monkeypatch) -> None:
    class FakeAkshare:
        @staticmethod
        def stock_zh_a_spot_tx():
            raise ConnectionError("remote closed")

        @staticmethod
        def stock_zh_a_spot_em():
            return pd.DataFrame(
                [{"代码": "000001", "名称": "平安银行", "最新价": "10", "成交额": "100000000"}]
            )

        @staticmethod
        def stock_zh_a_spot():
            raise AssertionError("东方财富成功后不应调用新浪")

    provider = AkshareStockDataProvider(tmp_path, universe_retries=1)
    monkeypatch.setattr(provider, "_akshare", lambda: FakeAkshare)
    monkeypatch.setattr("mom_select.stock.data.datetime", type(
        "FrozenDateTime",
        (),
        {"now": staticmethod(lambda tz=None: type("Now", (), {"date": lambda self: date(2026, 8, 13)})())},
    ))

    result = provider.stock_universe(date(2026, 8, 13))

    assert result.loc[0, "code"] == "000001.XSHE"
    assert any("回退到东方财富" in warning for warning in provider.warnings)


def test_tencent_history_uses_amount_as_turnover() -> None:
    source = pd.DataFrame(
        [{
            "date": date(2026, 8, 13), "open": 9.1, "close": 9.2,
            "high": 9.3, "low": 9.0, "volume": 10_000_000,
            "turnover": 0.012, "amount": 92_000_000,
        }]
    )

    result = AkshareStockDataProvider._normalize_history(source, "腾讯")

    assert result.loc[0, "turnover"] == 92_000_000


def test_stock_history_uses_tencent_before_eastmoney(tmp_path, monkeypatch) -> None:
    calls = []

    class FakeAkshare:
        @staticmethod
        def stock_zh_a_hist_tx(**kwargs):
            calls.append("tencent")
            return pd.DataFrame(
                [{
                    "date": date(2026, 8, 13), "open": 9.1, "close": 9.2,
                    "high": 9.3, "low": 9.0, "volume": 10_000_000,
                    "turnover": 0.012, "amount": 92_000_000,
                }]
            )

        @staticmethod
        def stock_zh_a_hist(**kwargs):
            calls.append("eastmoney")
            raise AssertionError("腾讯成功后不应请求东方财富")

    provider = AkshareStockDataProvider(tmp_path)
    monkeypatch.setattr(provider, "_akshare", lambda: FakeAkshare)

    result = provider.history("600000.XSHG", date(2026, 8, 1), date(2026, 8, 13))

    assert calls == ["tencent"]
    assert result.loc[0, "turnover"] == 92_000_000
