from pathlib import Path

from mom_select.notifications import TelegramNotifier, build_notifiers
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


def test_telegram_notifier_keeps_proxy():
    notifier = TelegramNotifier("token", "chat", "socks5://127.0.0.1:1080")
    assert notifier.proxy.startswith("socks5://")


def test_build_notifiers_skips_incomplete_channels():
    settings = type("Settings", (), {"enabled": True, "channels": [{"type": "telegram"}]})()
    assert build_notifiers(settings) == []
