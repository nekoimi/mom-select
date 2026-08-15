from datetime import date, datetime
from types import SimpleNamespace

from mom_select.stock import cli


def test_historical_date_can_run_online_with_cached_universe(monkeypatch) -> None:
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 8, 15, 16, 0, tzinfo=tz)

    captured = {}

    def fake_run(request):
        captured["request"] = request
        return SimpleNamespace(
            markdown="report.md",
            json="report.json",
            html="report.html",
            image="report.png",
        )

    monkeypatch.setattr(cli, "datetime", FrozenDateTime)
    monkeypatch.setattr(cli, "run", fake_run)

    cli.main(["--date", "2026-08-14"])

    assert captured["request"].as_of == date(2026, 8, 14)
    assert captured["request"].offline is False
