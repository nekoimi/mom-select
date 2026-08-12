from datetime import date
from pathlib import Path

import pandas as pd

from mom_select.data import DataProviderError, EastmoneyDataProvider


def test_history_preserves_datetime_when_initial_cache_is_empty(
    tmp_path: Path, monkeypatch,
) -> None:
    provider = EastmoneyDataProvider(tmp_path)
    fetched = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-08-10", "2026-08-11"]),
            "open": [1.0, 1.1],
            "close": [1.1, 1.2],
            "high": [1.1, 1.2],
            "low": [1.0, 1.1],
            "volume": [100.0, 120.0],
            "turnover": [110.0, 144.0],
        }
    )
    monkeypatch.setattr(provider, "_fetch", lambda code, start, end: fetched)

    result = provider.history("510300.XSHG", date(2026, 8, 11), date(2026, 8, 11))

    assert len(result) == 1
    assert result.iloc[0]["date"].date() == date(2026, 8, 11)


def test_primary_fetch_retries_transient_failure(tmp_path: Path, monkeypatch) -> None:
    provider = EastmoneyDataProvider(tmp_path, retries=2)
    calls = 0

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self):
            return (
                b'{"data":{"name":"test","klines":'
                b'["2026-08-11,1,1.1,1.2,0.9,100,110,0,0,0,0"]}}'
            )

    def flaky_urlopen(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ConnectionError("temporary")
        return Response()

    monkeypatch.setattr("mom_select.data.urlopen", flaky_urlopen)
    monkeypatch.setattr("mom_select.data.time.sleep", lambda _: None)

    result = provider._fetch_eastmoney(
        "510300.XSHG", date(2026, 8, 11), date(2026, 8, 11)
    )

    assert calls == 2
    assert len(result) == 1


def test_tencent_history_request_keeps_param_commas_unescaped(
    tmp_path: Path, monkeypatch
) -> None:
    provider = EastmoneyDataProvider(tmp_path)
    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self):
            return b'{"data":{"sz159768":{"day":[["2026-08-11","1","1.1","1.2","0.9","100"]]}}}'

    def fake_urlopen(request, **kwargs):
        captured["url"] = request.full_url
        return Response()

    monkeypatch.setattr("mom_select.data.urlopen", fake_urlopen)

    provider._fetch_tencent("159768.XSHE", date(2026, 8, 11), date(2026, 8, 11))

    assert "param=sz159768,day,2026-08-11,2026-08-11,1000,qfq" in captured["url"]


def test_yahoo_history_maps_daily_bars(tmp_path: Path, monkeypatch) -> None:
    provider = EastmoneyDataProvider(tmp_path)
    payload = {
        "chart": {
            "result": [
                {
                    "timestamp": [1786377600],
                    "indicators": {
                        "quote": [
                            {
                                "open": [1.0],
                                "close": [1.1],
                                "high": [1.2],
                                "low": [0.9],
                                "volume": [1000],
                            }
                        ]
                    },
                }
            ]
        }
    }
    monkeypatch.setattr(provider, "_request_json", lambda request: payload)

    result = provider._fetch_yahoo(
        "159768.XSHE", date(2026, 8, 10), date(2026, 8, 10)
    )

    assert len(result) == 1
    assert result.iloc[0]["close"] == 1.1
    assert result.iloc[0]["turnover"] == 1050.0
    assert any("Yahoo备用日线" in warning for warning in provider.warnings)


def test_close_snapshot_uses_returned_price_precision(tmp_path: Path, monkeypatch) -> None:
    provider = EastmoneyDataProvider(tmp_path)
    payload = {
        "data": {
            "f43": 9103,
            "f44": 9121,
            "f45": 9051,
            "f46": 9070,
            "f47": 5861954,
            "f48": 5324529499.0,
            "f58": "黄金ETF华安",
            "f59": 3,
        }
    }
    monkeypatch.setattr(provider, "_request_json", lambda request: payload)

    result = provider._fetch_close_snapshot("518880.XSHG", date(2026, 8, 12))

    assert result.iloc[0]["close"] == 9.103
    assert result.iloc[0]["volume"] == 586_195_400
    assert provider.security_name("518880.XSHG") == "黄金ETF华安"


def test_batch_snapshot_maps_exchange_and_name(tmp_path: Path, monkeypatch) -> None:
    provider = EastmoneyDataProvider(tmp_path)
    values = [""] * 38
    values[1] = "黄金ETF华安"
    values[2] = "518880"
    values[3] = "9.103"
    values[5] = "9.070"
    values[30] = "20260812161456"
    values[33] = "9.121"
    values[34] = "9.051"
    values[35] = "9.103/5861954/5324529499"
    values[36] = "5861954"
    body = f'v_sh518880="{"~".join(values)}";'.encode("gb18030")

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self):
            return body

    monkeypatch.setattr("mom_select.data.urlopen", lambda *args, **kwargs: Response())

    result = provider.supplement_close_snapshots(["518880.XSHG"], date(2026, 8, 12))

    assert result["518880.XSHG"].iloc[0]["close"] == 9.103
    assert provider.security_name("518880.XSHG") == "黄金ETF华安"

    offline_provider = EastmoneyDataProvider(tmp_path, offline=True)
    assert offline_provider.security_name("518880.XSHG") == "黄金ETF华安"
    assert not offline_provider.warnings


def test_etf_universe_reads_and_writes_cache(tmp_path: Path, monkeypatch) -> None:
    provider = EastmoneyDataProvider(tmp_path)
    payload = {
        "data": {
            "total": 2,
            "diff": [
                {"f12": "159768", "f13": 0, "f14": "房地产ETF银华"},
                {"f12": "512400", "f13": 1, "f14": "有色ETF"},
            ],
        }
    }
    calls = []

    def fake_request(request):
        calls.append(request)
        return payload

    monkeypatch.setattr(provider, "_request_json", fake_request)

    result = provider.etf_universe()

    assert result["code"].tolist() == ["159768.XSHE", "512400.XSHG"]
    assert provider.security_name("159768.XSHE") == "房地产ETF银华"
    assert calls
    assert (tmp_path / "_etf_universe.csv").exists()


def test_history_refetches_when_cached_window_does_not_cover_start(
    tmp_path: Path, monkeypatch
) -> None:
    provider = EastmoneyDataProvider(tmp_path)
    cached = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-08-10", "2026-08-11"]),
            "open": [1.0, 1.1],
            "close": [1.0, 1.1],
            "high": [1.0, 1.1],
            "low": [1.0, 1.1],
            "volume": [100.0, 110.0],
            "turnover": [100.0, 121.0],
        }
    )
    cached.to_csv(tmp_path / "510300.XSHG.csv", index=False)
    fetched = pd.concat(
        [
            pd.DataFrame(
                {
                    "date": pd.to_datetime(["2026-08-01"]),
                    "open": [0.9],
                    "close": [0.9],
                    "high": [0.9],
                    "low": [0.9],
                    "volume": [90.0],
                    "turnover": [81.0],
                }
            ),
            cached,
        ],
        ignore_index=True,
    )
    requested = {}

    def fetch(code, start, end):
        requested["start"] = start
        return fetched

    monkeypatch.setattr(provider, "_fetch", fetch)

    result = provider.history("510300.XSHG", date(2026, 8, 1), date(2026, 8, 11))

    assert result.iloc[0]["date"].date() == date(2026, 8, 1)
    assert requested["start"] == date(2026, 8, 1)


def test_history_uses_sufficient_cache_when_calendar_start_is_missing(
    tmp_path: Path, monkeypatch
) -> None:
    provider = EastmoneyDataProvider(tmp_path)
    dates = pd.bdate_range("2026-07-01", "2026-08-11")
    cached = pd.DataFrame(
        {
            "date": dates,
            "open": 1.0,
            "close": 1.0,
            "high": 1.0,
            "low": 1.0,
            "volume": 100.0,
            "turnover": 100.0,
        }
    )
    cached.to_csv(tmp_path / "510300.XSHG.csv", index=False)

    def fail_fetch(*args, **kwargs):
        raise AssertionError("sufficient cache should not fetch network data")

    monkeypatch.setattr(provider, "_fetch", fail_fetch)

    result = provider.history("510300.XSHG", date(2026, 6, 1), date(2026, 8, 11))

    assert len(result) == len(cached)
    assert result.iloc[0]["date"].date() == date(2026, 7, 1)
    assert any("未覆盖请求起始日" in warning for warning in provider.warnings)


def test_history_returns_current_short_cache_when_refetch_fails(
    tmp_path: Path, monkeypatch
) -> None:
    provider = EastmoneyDataProvider(tmp_path)
    cached = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-07-27", "2026-07-28"]),
            "open": [1.0, 1.0],
            "close": [1.0, 1.0],
            "high": [1.0, 1.0],
            "low": [1.0, 1.0],
            "volume": [100.0, 100.0],
            "turnover": [100.0, 100.0],
        }
    )
    cached.to_csv(tmp_path / "510300.XSHG.csv", index=False)

    monkeypatch.setattr(
        provider,
        "_fetch",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            DataProviderError("network unavailable")
        ),
    )

    result = provider.history("510300.XSHG", date(2026, 7, 1), date(2026, 7, 28))

    assert len(result) == 2
    assert any("按历史长度筛选" in warning for warning in provider.warnings)
