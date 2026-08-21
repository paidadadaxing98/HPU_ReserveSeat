import pytest

import json

from seat_assistant.config import MAX_ACCOUNTS, AccountSettings, Settings, load_account_settings, load_accounts, load_settings


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


def test_load_settings_reads_qwen_captcha_model_configuration(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SEAT_CAPTCHA_LLM_ENABLED", "true")
    monkeypatch.setenv("SEAT_CAPTCHA_LLM_API_KEY", "qwen-secret")
    monkeypatch.setenv("SEAT_CAPTCHA_LLM_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    monkeypatch.setenv("SEAT_CAPTCHA_LLM_MODEL", "qwen3.7-flash")
    monkeypatch.setenv("SEAT_CAPTCHA_LLM_TIMEOUT_SECONDS", "20")
    monkeypatch.setenv("SEAT_CAPTCHA_LLM_MAX_ATTEMPTS", "2")

    settings = load_settings()

    assert settings.captcha_llm_enabled is True
    assert settings.captcha_llm_api_key == "qwen-secret"
    assert settings.captcha_llm_base_url.endswith("/v1")
    assert settings.captcha_llm_model == "qwen3.7-flash"
    assert settings.captcha_llm_timeout_seconds == 20.0
    assert settings.captcha_llm_max_attempts == 2


def test_load_settings_rejects_enabled_captcha_llm_without_api_key(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SEAT_CAPTCHA_LLM_ENABLED", "true")
    monkeypatch.delenv("SEAT_CAPTCHA_LLM_API_KEY", raising=False)

    with pytest.raises(ValueError, match="验证码模型已启用但 API Key 为空"):
        load_settings()


def test_load_accounts_defaults_to_legacy_single_account(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SEAT_ACCOUNT", "student-a")
    monkeypatch.setenv("SEAT_PASSWORD", "secret")

    accounts = load_accounts()

    assert len(accounts) == 1
    assert accounts[0].id == "default"
    assert accounts[0].account == "student-a"
    assert accounts[0].profile_path == (tmp_path / ".browser-profile").resolve()
    assert accounts[0].db_path == (tmp_path / "seat_assistant.db").resolve()


def test_legacy_single_account_does_not_require_initialization(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SEAT_ACCOUNT", "student-a")
    monkeypatch.setenv("SEAT_PASSWORD", "secret")

    settings = load_account_settings()

    assert settings.require_initialization is False
    assert settings.location_preference == {
        "library": "南校区第二图书馆",
        "floor": "",
        "room": "",
    }


def test_load_accounts_reads_json_and_derives_isolated_paths(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "accounts.json").write_text(json.dumps({
        "accounts": [{"id": "alice", "account": "1001", "password": "secret"}]
    }), encoding="utf-8")

    accounts = load_accounts()

    assert accounts == [AccountSettings(
        id="alice",
        account="1001",
        password="secret",
        profile_path=(tmp_path / "accounts" / "alice" / "browser-profile").resolve(),
        db_path=(tmp_path / "accounts" / "alice" / "seat_assistant.db").resolve(),
    )]


def test_load_accounts_resolves_initialization_preferences_and_inheritance(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    payload = {
        "accounts": [
            {
                "id": "account01",
                "account": "1001",
                "password": "secret-a",
                "initialization": {
                    "preferred_seats": ["169", "168", "170"],
                    "periods": {
                        "morning": {
                            "arrival_window": ["08:20", "09:20"],
                            "departure_window": ["11:30", "13:00"],
                            "default_arrival": "08:50",
                        }
                    },
                },
            },
            {
                "id": "account02",
                "account": "1002",
                "password": "secret-b",
                "initialization": {"inherits_from": "account01"},
            },
        ]
    }
    (tmp_path / "accounts.json").write_text(json.dumps(payload), encoding="utf-8")

    accounts = load_accounts()

    assert accounts[0].preferred_seats == ("169", "168", "170")
    assert accounts[1].preferred_seats == accounts[0].preferred_seats
    assert accounts[1].periods["morning"].arrival_window == ("08:20", "09:20")
    assert accounts[1].periods["morning"].default_arrival == "08:50"


def test_load_account_settings_carries_initialization_preferences(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "accounts.json").write_text(json.dumps({"accounts": [{
        "id": "alice", "account": "1001", "password": "secret",
        "initialization": {"preferred_seats": ["169", "168"]},
    }]}), encoding="utf-8")

    settings = load_account_settings("alice")

    assert settings.preferred_seats == ("169", "168")


def test_load_account_settings_reads_structured_location_preference(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "accounts.json").write_text(json.dumps({"accounts": [{
        "id": "alice", "account": "1001", "password": "secret",
        "initialization": {
            "library": "南校区第二图书馆",
            "floor": "4F",
            "room": "4层计算机类借阅区",
            "seat_preference": {"mode": "random"},
        },
    }]}), encoding="utf-8")

    settings = load_account_settings("alice")

    assert settings.location_preference == {
        "library": "南校区第二图书馆",
        "floor": "4F",
        "room": "4层计算机类借阅区",
    }


def test_load_accounts_rejects_more_than_twenty_accounts(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    payload = {"accounts": [{"id": f"id-{i}", "account": str(i), "password": "secret"} for i in range(MAX_ACCOUNTS + 1)]}
    (tmp_path / "accounts.json").write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="最多支持 20 个账号"):
        load_accounts()


def test_load_accounts_rejects_duplicate_ids_and_accounts(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    duplicate_id = {"accounts": [
        {"id": "same", "account": "1001", "password": "secret"},
        {"id": "same", "account": "1002", "password": "secret"},
    ]}
    (tmp_path / "accounts.json").write_text(json.dumps(duplicate_id), encoding="utf-8")
    with pytest.raises(ValueError, match="账号 ID 重复"):
        load_accounts()

    duplicate_account = {"accounts": [
        {"id": "a", "account": "1001", "password": "secret"},
        {"id": "b", "account": "1001", "password": "secret"},
    ]}
    (tmp_path / "accounts.json").write_text(json.dumps(duplicate_account), encoding="utf-8")
    with pytest.raises(ValueError, match="学号重复"):
        load_accounts()


def test_load_accounts_rejects_shared_profile_or_database_paths(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    shared_profile = str(tmp_path / "shared-profile")
    shared_database = str(tmp_path / "shared.sqlite")
    (tmp_path / "accounts.json").write_text(json.dumps({"accounts": [
        {"id": "alice", "account": "1001", "password": "secret", "profile_path": shared_profile},
        {"id": "bob", "account": "1002", "password": "secret", "profile_path": shared_profile},
    ]}), encoding="utf-8")
    with pytest.raises(ValueError, match="浏览器会话目录重复"):
        load_accounts()

    (tmp_path / "accounts.json").write_text(json.dumps({"accounts": [
        {"id": "alice", "account": "1001", "password": "secret", "db_path": shared_database},
        {"id": "bob", "account": "1002", "password": "secret", "db_path": shared_database},
    ]}), encoding="utf-8")
    with pytest.raises(ValueError, match="数据库路径重复"):
        load_accounts()


def test_load_accounts_rejects_blank_credentials(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "accounts.json").write_text(json.dumps({
        "accounts": [{"id": "alice", "account": "", "password": "secret"}]
    }), encoding="utf-8")

    with pytest.raises(ValueError, match="账号和密码不能为空"):
        load_accounts()


def test_load_accounts_skips_disabled_blank_reservations(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "accounts.json").write_text(json.dumps({"accounts": [
        {"id": "account01", "account": "1001", "password": "secret", "enabled": True},
        {"id": "account02", "account": "", "password": "", "enabled": False},
    ]}), encoding="utf-8")

    accounts = load_accounts()

    assert [item.id for item in accounts] == ["account01"]


def test_load_accounts_rejects_empty_json_account_list(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "accounts.json").write_text('{"accounts": []}', encoding="utf-8")

    with pytest.raises(ValueError, match="至少配置一个账号"):
        load_accounts()


def test_settings_reject_success_limit_above_three():
    with pytest.raises(ValueError, match="1 到 3"):
        Settings(control_token="local-token", daily_success_limit=4)


def test_load_account_settings_requires_id_when_multiple_accounts_exist(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "accounts.json").write_text(json.dumps({"accounts": [
        {"id": "alice", "account": "1001", "password": "secret"},
        {"id": "bob", "account": "1002", "password": "secret"},
    ]}), encoding="utf-8")

    with pytest.raises(ValueError, match="配置了多个账号"):
        load_account_settings()
