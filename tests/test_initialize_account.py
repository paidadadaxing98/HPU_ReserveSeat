import asyncio
from seat_assistant.config import Settings
from scripts import initialize_account as initialize_module
from scripts.initialize_account import ReadOnlyAccountVerifier
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


def test_initialize_workflow_persists_wecom_aliases(tmp_path):
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
                "rooms_by_library": {"新图": ["4层新图一号自习室"]},
            }

    asyncio.run(run_interactive_initialization(
        account_id="alice",
        settings=Settings(control_token="local-token", db_path=str(tmp_path / "alice.sqlite")),
        config_path=config_path,
        verifier=FakeVerifier(),
        input_fn=iter(["1", "2", "张三, zs", "", "", "", ""]).__next__,
        output_fn=lambda _: None,
    ))

    saved = config_path.read_text(encoding="utf-8")
    assert '"wecom_aliases": [' in saved
    assert '"张三"' in saved
    assert '"zs"' in saved


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


def test_reinitializing_without_seat_rules_clears_old_rules(tmp_path):
    config_path = tmp_path / "accounts.json"
    config_path.write_text(
        '{"accounts":[{"id":"alice","enabled":true,"account":"1001",'
        '"password":"secret","initialization":{"seat_rules":["2-9-023"]}}]}',
        encoding="utf-8",
    )

    class FakeVerifier:
        async def verify(self):
            return {
                "home": True,
                "my_reservations": True,
                "capabilities": {"history": True},
                "library_catalog": ["老图", "新图"],
                "rooms_by_library": {"新图": ["7层新阅览室"]},
            }

    asyncio.run(run_interactive_initialization(
        account_id="alice",
        settings=Settings(control_token="local-token", db_path=str(tmp_path / "alice.sqlite")),
        config_path=config_path,
        verifier=FakeVerifier(),
        input_fn=iter(["3", "2", "1", "181 184", "", "", ""]).__next__,
        output_fn=lambda _: None,
    ))

    saved = config_path.read_text(encoding="utf-8")
    assert '"seat_rules": []' in saved


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


def test_read_only_verifier_returns_catalog_without_undefined_room_variable(monkeypatch, tmp_path):
    class FakePage:
        url = "https://seatlib.hpu.edu.cn/libseat/#/home"

        def on(self, *_args, **_kwargs):
            return None

        async def goto(self, *_args, **_kwargs):
            return None

        async def wait_for_timeout(self, *_args, **_kwargs):
            return None

    class FakeContext:
        def __init__(self):
            self.pages = [FakePage()]

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return None

        async def new_page(self):
            return self.pages[0]

    class FakeBrowser:
        def __init__(self, *_args, **_kwargs):
            self.context = FakeContext()

        async def __aenter__(self):
            return self.context

        async def __aexit__(self, *_exc):
            return None

    monkeypatch.setattr(initialize_module, "LockedBrowser", FakeBrowser)
    monkeypatch.setattr(initialize_module, "login_if_configured", lambda *_args: _true_async())
    monkeypatch.setattr(initialize_module, "wait_for_authenticated_page", lambda *_args, **_kwargs: _noop_async())
    monkeypatch.setattr(
        initialize_module,
        "visible_library_names",
        lambda *_args: _value_async(["第一图书馆", "第二图书馆"]),
    )
    monkeypatch.setattr(initialize_module, "select_library", lambda *_args: _noop_async())
    monkeypatch.setattr(
        initialize_module,
        "visible_room_names",
        lambda *_args: _value_async(["一层自习室"]),
    )
    monkeypatch.setattr(
        initialize_module,
        "fetch_user_reservations_with_capabilities",
        lambda *_args: _value_async(([], {"my_reservations": True, "history": True})),
    )
    settings = type(
        "Settings",
        (),
        {"profile_path": str(tmp_path / "profile"), "login_url": "https://example.test"},
    )()
    verifier = ReadOnlyAccountVerifier(settings)
    result = asyncio.run(verifier.verify())

    assert result["seat_catalog"] == ["一层自习室"]


async def _true_async():
    return True


async def _noop_async():
    return None


async def _value_async(value):
    return value
