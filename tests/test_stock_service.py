from datetime import date
from pathlib import Path

import pandas as pd

from mom_select.stock.models import StockRunRequest, StockStrategyConfig
from mom_select.stock import service
from mom_select.stock.service import _public_data_warnings


def test_stock_service_marks_stale_history_as_failed(tmp_path, monkeypatch) -> None:
    universe = pd.DataFrame(
        [["600001.XSHG", "样例", "沪深主板", 20.0, 0.03, 4e8, 0.05, "正常", "2026-08-13"]],
        columns=[
            "code", "name", "board", "price", "change_pct", "turnover",
            "turnover_rate", "trade_status", "quote_date",
        ],
    )
    stale = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-08-12"]),
            "open": [20.0],
            "close": [20.0],
            "high": [20.0],
            "low": [20.0],
            "volume": [1e6],
            "turnover": [1e8],
        }
    )
    benchmark = stale.copy()
    benchmark["date"] = pd.to_datetime(["2026-08-13"])

    class StockProvider:
        warnings = []

        def __init__(self, *args, **kwargs):
            pass

        def stock_universe(self, as_of):
            return universe

        def histories(self, codes, start, end):
            return {codes[0]: stale}, {}

    class BenchmarkProvider:
        warnings = set()

        def __init__(self, *args, **kwargs):
            pass

        def history(self, code, start, end):
            return benchmark

    captured = {}

    def fake_write(report, report_dir):
        captured["report"] = report
        return Path(report_dir) / "report"

    monkeypatch.setattr(service, "AkshareStockDataProvider", StockProvider)
    monkeypatch.setattr(service, "EastmoneyDataProvider", BenchmarkProvider)
    monkeypatch.setattr(service, "write_reports", fake_write)

    service.run(
        StockRunRequest(
            as_of=date(2026, 8, 13),
            cache_dir=tmp_path / "cache",
            report_dir=tmp_path / "reports",
            strategy=StockStrategyConfig(
                minimum_universe_size=1,
                minimum_data_coverage=0,
            ),
        )
    )

    report = captured["report"]
    assert report.failed_codes["600001.XSHG"].startswith("前复权日线未覆盖目标交易日")
    assert report.analyzed_count == 0
    assert report.entry_candidates == []
    assert not report.actionable


def test_public_warnings_hide_provider_error_details() -> None:
    diagnostics = [
        "全市场A股快照已回退到腾讯；东方财富第1次: "
        "https://example.invalid RemoteDisconnected",
        "600001.XSHG前复权日线更新失败，使用缓存: ConnectionError secret detail",
        "600002.XSHG前复权日线更新失败，使用缓存: Timeout https://example.invalid",
        "600003.XSHG前复权日线已回退到东方财富",
    ]

    warnings = _public_data_warnings(diagnostics, ["指数使用Yahoo备用日线"])
    rendered = "；".join(warnings)

    assert "全市场A股快照使用腾讯备用数据源" in warnings
    assert "2只股票的前复权日线使用本地缓存" in warnings
    assert "1只股票的前复权日线使用备用数据源" in warnings
    assert "市场基准指数行情使用备用数据源或缓存" in warnings
    assert "https://" not in rendered
    assert "RemoteDisconnected" not in rendered
    assert "ConnectionError" not in rendered
