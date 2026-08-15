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


def test_historical_stock_universe_uses_same_day_cache_while_online(
    tmp_path, monkeypatch
) -> None:
    provider = AkshareStockDataProvider(tmp_path)
    cached = pd.DataFrame(
        [["600000.XSHG", "浦发银行", "沪深主板", 10.0, 1e8, "正常", "2020-08-13"]],
        columns=["code", "name", "board", "price", "turnover", "trade_status", "quote_date"],
    )
    cached.to_csv(provider.universe_path, index=False)
    monkeypatch.setattr(
        provider,
        "_fetch_universe",
        lambda as_of: pytest.fail("历史日期不应请求当前全市场快照"),
    )

    result = provider.stock_universe(date(2020, 8, 13))

    assert result.loc[0, "code"] == "600000.XSHG"


def test_historical_stock_universe_requires_same_day_cache_while_online(tmp_path) -> None:
    provider = AkshareStockDataProvider(tmp_path)

    with pytest.raises(DataProviderError, match="缺少同日全市场快照"):
        provider.stock_universe(date(2020, 8, 13))


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


def test_stock_history_uses_eastmoney_before_tencent(tmp_path, monkeypatch) -> None:
    calls = []

    class FakeAkshare:
        @staticmethod
        def stock_zh_a_hist_tx(**kwargs):
            raise AssertionError("东方财富成功后不应请求腾讯")

        @staticmethod
        def stock_zh_a_hist(**kwargs):
            calls.append(("eastmoney", kwargs))
            return pd.DataFrame(
                [{
                    "日期": date(2026, 8, 13), "开盘": 9.1, "收盘": 9.2,
                    "最高": 9.3, "最低": 9.0, "成交量": 10_000_000,
                    "成交额": 92_000_000,
                }]
            )

    provider = AkshareStockDataProvider(tmp_path)
    monkeypatch.setattr(provider, "_akshare", lambda: FakeAkshare)

    result = provider.history("600000.XSHG", date(2026, 8, 1), date(2026, 8, 13))

    assert calls[0][0] == "eastmoney"
    assert calls[0][1]["timeout"] == 15
    assert result.loc[0, "turnover"] == 92_000_000


def test_stock_history_refetches_corrupt_cache(tmp_path, monkeypatch) -> None:
    class FakeAkshare:
        @staticmethod
        def stock_zh_a_hist_tx(**kwargs):
            raise AssertionError("东方财富成功后不应请求腾讯")

        @staticmethod
        def stock_zh_a_hist(**kwargs):
            return pd.DataFrame(
                [{
                    "日期": date(2026, 8, 13), "开盘": 9.1, "收盘": 9.2,
                    "最高": 9.3, "最低": 9.0, "成交量": 10_000_000,
                    "成交额": 92_000_000,
                }]
            )

    provider = AkshareStockDataProvider(tmp_path)
    provider._history_path("600000.XSHG").write_bytes(b"\x00" * 128)
    monkeypatch.setattr(provider, "_akshare", lambda: FakeAkshare)

    result = provider.history("600000.XSHG", date(2026, 8, 1), date(2026, 8, 13))

    assert result.loc[0, "date"].date() == date(2026, 8, 13)
    assert any("缓存损坏" in warning for warning in provider.warnings)
    assert provider._history_path("600000.XSHG").read_bytes().startswith(b"date,")


def test_stock_history_uses_bounded_tencent_fallback(tmp_path, monkeypatch) -> None:
    calls = []

    class FakeAkshare:
        @staticmethod
        def stock_zh_a_hist(**kwargs):
            raise TimeoutError("eastmoney timeout")

    provider = AkshareStockDataProvider(tmp_path)
    monkeypatch.setattr(provider, "_akshare", lambda: FakeAkshare)
    monkeypatch.setattr(
        provider._bounded_history_provider,
        "tencent_qfq_history",
        lambda code, start, end: calls.append((code, start, end))
        or pd.DataFrame(
            [{
                "date": pd.Timestamp("2026-08-13"), "open": 9.1, "close": 9.2,
                "high": 9.3, "low": 9.0, "volume": 10_000_000,
                "turnover": 92_000_000,
            }]
        ),
    )

    result = provider.history("600000.XSHG", date(2026, 8, 1), date(2026, 8, 13))

    assert result.loc[0, "close"] == 9.2
    assert calls[0][0] == "600000.XSHG"
    assert any("已回退到腾讯" in warning for warning in provider.warnings)


def test_stock_history_does_not_refetch_short_cache_covering_end(tmp_path, monkeypatch) -> None:
    provider = AkshareStockDataProvider(tmp_path)
    cached = pd.DataFrame(
        [{
            "date": pd.Timestamp("2026-08-13"), "open": 9.1, "close": 9.2,
            "high": 9.3, "low": 9.0, "volume": 10_000_000,
            "turnover": 92_000_000,
        }]
    )
    cached.to_csv(provider._history_path("600000.XSHG"), index=False)
    monkeypatch.setattr(
        provider,
        "_akshare",
        lambda: pytest.fail("已覆盖目标日的缓存不应再次请求网络"),
    )

    result = provider.history("600000.XSHG", date(2026, 8, 1), date(2026, 8, 13))

    assert len(result) == 1
    assert result.loc[0, "date"].date() == date(2026, 8, 13)
