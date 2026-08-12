import argparse
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import pytest

from mom_select import cli
from mom_select.cli import _append_snapshot, _intraday_volume_multiplier, build_parser
from mom_select.config import DEFAULT_POOL_FILE
from mom_select.data import EastmoneyDataProvider, SnapshotBatch


def test_intraday_volume_multiplier_at_1305() -> None:
    assert _intraday_volume_multiplier(datetime(2026, 8, 12, 13, 5)) == 240 / 125


def test_intraday_is_the_default_mode() -> None:
    args = build_parser().parse_args([])
    assert args.mode == "intraday"
    assert not args.debug


def test_debug_flag_keeps_intraday_mode() -> None:
    args = build_parser().parse_args(["--debug"])
    assert args.mode == "intraday"
    assert args.debug


def test_intraday_volume_multiplier_caps_at_full_trading_day() -> None:
    assert _intraday_volume_multiplier(datetime(2026, 8, 12, 15, 5)) == 1.0


def test_append_snapshot_replaces_same_day_bar() -> None:
    history = pd.DataFrame(
        {"date": pd.to_datetime(["2026-08-11"]), "close": [1.0]}
    )
    snapshot = pd.DataFrame(
        {"date": pd.to_datetime(["2026-08-12"]), "close": [1.1]}
    )

    result = _append_snapshot(history, snapshot)

    assert result["close"].tolist() == [1.0, 1.1]


def test_intraday_mode_runs_with_realtime_snapshots_without_saving_state(
    tmp_path: Path, monkeypatch,
) -> None:
    cache_dir = Path("data/cache")
    base_provider = EastmoneyDataProvider(cache_dir, offline=True)

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            value = cls(2026, 8, 12, 13, 5)
            return value.replace(tzinfo=tz) if tz else value

    class FakeProvider:
        def __init__(self, *args, **kwargs):
            self.warnings = set()
            self._names = base_provider._names

        def histories(self, codes, start, end):
            return base_provider.histories(codes, start, end)

        def security_name(self, code):
            return base_provider.security_name(code)

        def etf_universe(self):
            return pd.DataFrame({"code": [], "name": []})

        def realtime_snapshots(self, codes, trading_date):
            frames = {}
            quote_times = {}
            for code in codes:
                frame = base_provider.history(code, trading_date, trading_date)
                if frame.empty:
                    continue
                frames[code] = frame.tail(1).copy()
                quote_times[code] = datetime(2026, 8, 12, 13, 5)
            return SnapshotBatch(frames, quote_times)

    captured = {}

    def fake_write_reports(report, report_dir):
        captured["report"] = report
        return None

    monkeypatch.setattr(cli, "datetime", FrozenDateTime)
    monkeypatch.setattr(cli, "EastmoneyDataProvider", FakeProvider)
    monkeypatch.setattr(cli, "write_reports", fake_write_reports)
    state_file = tmp_path / "market-state.json"
    args = argparse.Namespace(
        date=date(2026, 8, 12),
        mode="intraday",
        portfolio=None,
        pool=DEFAULT_POOL_FILE,
        cache_dir=cache_dir,
        report_dir=tmp_path,
        state_file=state_file,
        offline=False,
        debug=False,
        workers=8,
        allow_incomplete_day=False,
        no_save_state=False,
        fixed_pool_only=True,
    )

    cli.run(args)

    report = captured["report"]
    assert report.run_mode == "intraday"
    assert report.signal_time == "2026-08-12 13:05:00"
    assert report.analyzed_count == 114
    assert report.actionable
    assert not state_file.exists()


def test_intraday_mode_rejects_offline_execution(tmp_path: Path) -> None:
    args = argparse.Namespace(
        date=date.today(),
        mode="intraday",
        portfolio=None,
        pool=DEFAULT_POOL_FILE,
        cache_dir=Path("data/cache"),
        report_dir=tmp_path,
        state_file=tmp_path / "market-state.json",
        offline=True,
        debug=False,
        workers=8,
        allow_incomplete_day=False,
        no_save_state=False,
        fixed_pool_only=True,
    )

    with pytest.raises(RuntimeError, match="必须在线"):
        cli.run(args)


def test_historical_debug_uses_daily_bars_without_realtime_snapshot(
    tmp_path: Path, monkeypatch,
) -> None:
    cache_dir = Path("data/cache")
    base_provider = EastmoneyDataProvider(cache_dir, offline=True)

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            value = cls(2026, 8, 12, 20, 0)
            return value.replace(tzinfo=tz) if tz else value

    class FakeProvider:
        def __init__(self, *args, **kwargs):
            self.warnings = set()

        def histories(self, codes, start, end):
            return base_provider.histories(codes, start, end)

        def security_name(self, code):
            return base_provider.security_name(code)

        def etf_universe(self):
            return pd.DataFrame({"code": [], "name": []})

        def realtime_snapshots(self, codes, trading_date):
            raise AssertionError("历史DEBUG不应请求实时快照")

    captured = {}

    def fake_write_reports(report, report_dir):
        captured["report"] = report
        return None

    monkeypatch.setattr(cli, "datetime", FrozenDateTime)
    monkeypatch.setattr(cli, "EastmoneyDataProvider", FakeProvider)
    monkeypatch.setattr(cli, "write_reports", fake_write_reports)
    state_file = tmp_path / "market-state.json"
    args = argparse.Namespace(
        date=date(2026, 8, 11),
        mode="intraday",
        portfolio=None,
        pool=DEFAULT_POOL_FILE,
        cache_dir=cache_dir,
        report_dir=tmp_path,
        state_file=state_file,
        offline=True,
        debug=True,
        workers=8,
        allow_incomplete_day=False,
        no_save_state=False,
        fixed_pool_only=True,
    )

    cli.run(args)

    report = captured["report"]
    assert report.as_of == date(2026, 8, 11)
    assert report.signal_time == "2026-08-11 13:05:00"
    assert report.historical_simulation
    assert not report.actionable
    assert any("完整日K模拟13:05" in item for item in report.warnings)
    assert not state_file.exists()
