from datetime import date
from pathlib import Path

import pandas as pd

from mom_select.data import EastmoneyDataProvider


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
    assert "部分ETF的最新日K由腾讯批量收盘行情快照补齐" in offline_provider.warnings
