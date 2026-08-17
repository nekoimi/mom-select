from pathlib import Path

import pytest

from mom_select.notifications import TelegramNotifier, WeChatNotifier, build_notifiers
from mom_select.settings import load_settings


def test_yaml_settings_expand_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("WEBHOOK_TEST", "https://example.invalid/hook")
    config = tmp_path / "config.yaml"
    config.write_text(
        "paths:\n  report_dir: reports\nnotifications:\n  enabled: true\n  channels:\n    - type: wechat\n      webhook: ${WEBHOOK_TEST}\n",
        encoding="utf-8",
    )
    settings = load_settings(config)
    assert settings.report_dir == tmp_path / "reports"
    assert settings.notification.channels[0]["webhook"] == "https://example.invalid/hook"


def test_yaml_settings_loads_independent_stock_task(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(
        "tasks:\n"
        "  etf:\n"
        "    enabled: false\n"
        "  stock:\n"
        "    enabled: true\n"
        "    schedule:\n"
        "      hour: 15\n"
        "      minute: 30\n"
        "    paths:\n"
        "      cache_dir: data/stock-cache\n"
        "    strategy:\n"
        "      trend_windows: [20, 60, 120]\n"
        "      price_upper_bound_inclusive: 50\n",
        encoding="utf-8",
    )

    settings = load_settings(config)

    assert not settings.etf_enabled
    assert settings.stock.enabled
    assert settings.stock.schedule.hour == 15
    assert settings.stock.schedule.minute == 30
    assert settings.stock.cache_dir == tmp_path / "data/stock-cache"
    assert settings.stock.strategy.trend_windows == (20, 60, 120)
    assert settings.stock.strategy.price_upper_bound_inclusive == 50


def test_telegram_notifier_keeps_proxy():
    notifier = TelegramNotifier("token", "chat", "socks5://127.0.0.1:1080")
    assert notifier.proxy.startswith("socks5://")


def test_build_notifiers_skips_incomplete_channels():
    settings = type("Settings", (), {"enabled": True, "channels": [{"type": "telegram"}]})()
    assert build_notifiers(settings) == []


def test_wechat_notifier_checks_both_api_responses(tmp_path: Path, monkeypatch):
    image = tmp_path / "report.png"
    image.write_bytes(b"png")
    calls = []

    class Response:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"errcode": 0, "errmsg": "ok"}

    def fake_post(url, **kwargs):
        calls.append(kwargs["json"]["msgtype"])
        return Response()

    monkeypatch.setattr("mom_select.notifications.requests.post", fake_post)

    WeChatNotifier("https://example.invalid/hook").send(image, "subject", "text")

    assert calls == ["text", "image"]


def test_wechat_notifier_reports_api_error(tmp_path: Path, monkeypatch):
    image = tmp_path / "report.png"
    image.write_bytes(b"png")

    class Response:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"errcode": 93000, "errmsg": "invalid webhook"}

    monkeypatch.setattr("mom_select.notifications.requests.post", lambda *a, **k: Response())

    with pytest.raises(RuntimeError, match="invalid webhook"):
        WeChatNotifier("https://example.invalid/hook").send(image, "subject", "text")
