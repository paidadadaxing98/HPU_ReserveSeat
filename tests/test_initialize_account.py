import asyncio

from seat_assistant.config import Settings
from seat_assistant.initialization import run_interactive_initialization


def test_initialize_workflow_persists_preferences_without_booking_calls(tmp_path):
    config_path = tmp_path / "accounts.json"
    config_path.write_text(
        '{"accounts":[{"id":"alice","enabled":true,"account":"1001",'
        '"password":"secret"}]}',
        encoding="utf-8",
    )

    class FakeVerifier:
        async def verify(self):
            return {
                "home": True,
                "my_reservations": True,
                "capabilities": {"history": True},
                "library_catalog": ["老图", "新图"],
                "rooms_by_library": {
                    "老图": ["老图自习室"],
                    "新图": ["4层新图一号自习室", "4层新图二号自习室"],
                },
            }

    calls = []

    class ForbiddenBookingAdapter:
        def reserve(self, *args):
            calls.append("reserve")
            raise AssertionError("初始化不得预约")

        def cancel(self, *args):
            calls.append("cancel")
            raise AssertionError("初始化不得取消预约")

    result = asyncio.run(run_interactive_initialization(
        account_id="alice",
        settings=Settings(control_token="local-token", db_path=str(tmp_path / "alice.sqlite")),
        config_path=config_path,
        verifier=FakeVerifier(),
        booking_adapter=ForbiddenBookingAdapter(),
        input_fn=iter(["2", "2", "4F", "", "", "", ""]).__next__,
        output_fn=lambda _: None,
    ))

    assert result["status"] == "ready"
    assert calls == []
    saved = config_path.read_text(encoding="utf-8")
    assert '"mode": "floor"' in saved
    assert '"library": "新图"' in saved
    assert '"room": ""' in saved
    assert '"arrival_window": [' in saved
    assert '"08:00"' in saved
    assert '"12:00"' in saved


def test_floor_initialization_does_not_ask_for_a_room_again(tmp_path):
    config_path = tmp_path / "accounts.json"
    config_path.write_text(
        '{"accounts":[{"id":"alice","enabled":true,"account":"1001","password":"secret"}]}',
        encoding="utf-8",
    )

    class FakeVerifier:
        async def verify(self):
            return {
                "home": True,
                "my_reservations": True,
                "capabilities": {"history": True},
                "library_catalog": ["老图", "新图"],
                "rooms_by_library": {
                    "新图": ["4层一号自习室", "4层二号自习室"],
                },
            }

    prompts = []
    answers = iter(["2", "2", "4F", "", "", ""])

    def input_fn(prompt=""):
        prompts.append(prompt)
        return next(answers)

    asyncio.run(run_interactive_initialization(
        account_id="alice",
        settings=Settings(control_token="local-token", db_path=str(tmp_path / "alice.sqlite")),
        config_path=config_path,
        verifier=FakeVerifier(),
        input_fn=input_fn,
        output_fn=lambda _: None,
    ))

    assert not any("阅览室编号" in prompt for prompt in prompts)
    saved = config_path.read_text(encoding="utf-8")
    assert '"floor": "4F"' in saved
    assert '"room": ""' in saved


def test_random_initialization_only_asks_for_library_and_windows(tmp_path):
    config_path = tmp_path / "accounts.json"
    config_path.write_text(
        '{"accounts":[{"id":"alice","enabled":true,"account":"1001","password":"secret"}]}',
        encoding="utf-8",
    )

    class FakeVerifier:
        async def verify(self):
            return {
                "home": True,
                "my_reservations": True,
                "capabilities": {"history": True},
                "library_catalog": ["老图", "新图"],
                "rooms_by_library": {"新图": ["4层自习室"]},
            }

    result = asyncio.run(run_interactive_initialization(
        account_id="alice",
        settings=Settings(control_token="local-token", db_path=str(tmp_path / "alice.sqlite")),
        config_path=config_path,
        verifier=FakeVerifier(),
        input_fn=iter(["1", "2", "", "", ""]).__next__,
        output_fn=lambda _: None,
    ))

    assert result["status"] == "ready"
    settings_text = config_path.read_text(encoding="utf-8")
    assert '"mode": "random"' in settings_text
    assert '"library": "新图"' in settings_text
    assert '"room": ""' in settings_text


def test_exact_seat_initialization_asks_library_then_room_then_seats(tmp_path):
    config_path = tmp_path / "accounts.json"
    config_path.write_text(
        '{"accounts":[{"id":"alice","enabled":true,"account":"1001","password":"secret"}]}',
        encoding="utf-8",
    )

    class FakeVerifier:
        async def verify(self):
            return {
                "home": True,
                "my_reservations": True,
                "capabilities": {"history": True},
                "library_catalog": ["老图", "新图"],
                "rooms_by_library": {"新图": ["4层自习室"]},
            }

    asyncio.run(run_interactive_initialization(
        account_id="alice",
        settings=Settings(control_token="local-token", db_path=str(tmp_path / "alice.sqlite")),
        config_path=config_path,
        verifier=FakeVerifier(),
        input_fn=iter(["3", "2", "1", "169 168", "", "", ""]).__next__,
        output_fn=lambda _: None,
    ))

    saved = config_path.read_text(encoding="utf-8")
    assert '"room": "4层自习室"' in saved
    assert '"preferred_seats": [\n          "169",\n          "168"\n        ]' in saved


def test_initialize_workflow_fails_when_library_catalog_is_missing(tmp_path):
    config_path = tmp_path / "accounts.json"
    config_path.write_text(
        '{"accounts":[{"id":"alice","enabled":true,"account":"1001","password":"secret"}]}',
        encoding="utf-8",
    )

    class FakeVerifier:
        async def verify(self):
            return {
                "home": True,
                "my_reservations": True,
                "capabilities": {"history": True},
                "library_catalog": [],
                "rooms_by_library": {},
            }

    result = asyncio.run(run_interactive_initialization(
        account_id="alice",
        settings=Settings(control_token="local-token", db_path=str(tmp_path / "alice.sqlite")),
        config_path=config_path,
        verifier=FakeVerifier(),
        input_fn=lambda *_: "",
        output_fn=lambda _: None,
    ))

    assert result["status"] == "failed"
    assert "图书馆" in result["message"]


def test_initialize_stops_before_resolving_rules_when_room_catalog_collection_failed(tmp_path):
    config_path = tmp_path / "accounts.json"
    config_path.write_text(
        '{"accounts":[{"id":"alice","enabled":true,"account":"1001",'
        '"password":"secret"}]}',
        encoding="utf-8",
    )

    class FakeVerifier:
        async def verify(self):
            return {
                "home": True,
                "my_reservations": True,
                "capabilities": {"history": True},
                "library_catalog": ["南校区第一图书馆", "南校区第二图书馆"],
                "rooms_by_library": {"南校区第一图书馆": ["一层自习室"]},
                "catalog_errors": {"南校区第二图书馆": "阅览室下拉项无法点击"},
            }

    result = asyncio.run(run_interactive_initialization(
        account_id="alice",
        settings=Settings(control_token="local-token", db_path=str(tmp_path / "alice.sqlite")),
        config_path=config_path,
        verifier=FakeVerifier(),
        seat_rule_values=["2-10-23"],
        output_fn=lambda _: None,
    ))

    assert result["status"] == "failed"
    assert "南校区第二图书馆" in result["message"]
    assert "阅览室下拉项无法点击" in result["message"]


def test_initialize_workflow_records_failed_state_when_read_only_verification_raises(tmp_path):
    config_path = tmp_path / "accounts.json"
    config_path.write_text(
        '{"accounts":[{"id":"alice","enabled":true,"account":"1001","password":"secret"}]}',
        encoding="utf-8",
    )

    class BrokenVerifier:
        async def verify(self):
            raise RuntimeError("我的预约接口不可用")

    result = asyncio.run(run_interactive_initialization(
        account_id="alice",
        settings=Settings(control_token="local-token", db_path=str(tmp_path / "alice.sqlite")),
        config_path=config_path,
        verifier=BrokenVerifier(),
        input_fn=lambda *_: "",
        output_fn=lambda _: None,
    ))

    assert result["status"] == "failed"
    assert "我的预约接口不可用" in result["message"]
