import json

from seat_assistant.main import build_service


def test_build_service_selects_the_requested_account(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "accounts.json").write_text(json.dumps({
        "accounts": [
            {"id": "alice", "account": "1001", "password": "secret-a"},
            {"id": "bob", "account": "1002", "password": "secret-b"},
        ]
    }), encoding="utf-8")

    settings, service = build_service("bob")

    assert settings.account_id == "bob"
    assert service.account_id == "bob"
    assert service.repo.account_id == "bob"
    assert settings.db_path == str((tmp_path / "accounts" / "bob" / "seat_assistant.db").resolve())


def test_build_service_loads_dotenv_before_resolving_account_settings(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "SEAT_ACCOUNT=1001\nSEAT_PASSWORD=secret\nSEAT_DRY_RUN=false\n",
        encoding="utf-8",
    )

    settings, _ = build_service()

    assert settings.account == "1001"
    assert settings.password == "secret"
    assert settings.dry_run is False
