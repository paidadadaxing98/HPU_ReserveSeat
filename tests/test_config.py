import pytest

from seat_assistant.config import Settings, load_settings


def test_settings_allow_blank_reservation_credentials():
    settings = Settings(control_token="local-token", account="", password="")
    assert settings.account == ""


def test_settings_reject_blank_control_token():
    with pytest.raises(ValueError):
        Settings(control_token="")


def test_settings_reject_multiple_daily_reservations():
    with pytest.raises(ValueError, match="只能为 1"):
        Settings(control_token="local-token", max_reservations_per_run=2)


def test_settings_bind_control_server_to_localhost_by_default():
    assert Settings(control_token="local-token").control_host == "127.0.0.1"


def test_load_settings_reads_wecom_webhook(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SEAT_WECOM_WEBHOOK", "https://example.test/webhook")

    settings = load_settings()

    assert settings.wecom_webhook == "https://example.test/webhook"
