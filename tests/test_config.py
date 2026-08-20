import pytest

from seat_assistant.config import Settings


def test_settings_allow_blank_reservation_credentials():
    settings = Settings(control_token="local-token", account="", password="")
    assert settings.account == ""


def test_settings_reject_blank_control_token():
    with pytest.raises(ValueError):
        Settings(control_token="")


def test_settings_bind_control_server_to_localhost_by_default():
    assert Settings(control_token="local-token").control_host == "127.0.0.1"
