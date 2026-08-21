"""Pure helpers and persistence utilities for one-time account initialization."""

import json
from datetime import datetime
from pathlib import Path

from .browser_session import run_initialization_verification
from .storage import Repository


PERIOD_NAMES = ("morning", "afternoon", "evening")
DEFAULT_PERIOD_WINDOWS = {
    "morning": ("08:00", "12:00"),
    "afternoon": ("14:30", "18:30"),
    "evening": ("19:30", "22:00"),
}


def initialization_summary(
    account_id: str,
    location: dict,
    seat_preference: dict,
    periods: dict[str, tuple[str, str]],
) -> str:
    location = location or {}
    library = str(location.get("library", "")).strip() or "未设置"
    floor = str(location.get("floor", "")).strip() or "随机楼层"
    room = str(location.get("room", "")).strip() or "自动分配阅览室"
    mode = str((seat_preference or {}).get("mode", "random")).strip().lower()
    seat_text = {
        "random": "随机空闲座位",
        "floor": f"楼层空闲座位（{seat_preference.get('floor', '')}）",
        "seats": "具体座位优先：" + "、".join(seat_preference.get("seats", [])),
    }.get(mode, "随机空闲座位")
    period_text = "；".join(
        f"{name}={periods[name][0]}-{periods[name][1]}"
        for name in PERIOD_NAMES
        if name in periods
    )
    return f"账号={account_id}；位置={library} / {floor} / {room}；座位={seat_text}；学习窗口={period_text}"


def initialization_status(login_verified: bool, home_verified: bool, my_reservations_verified: bool) -> str:
    return "ready" if all((login_verified, home_verified, my_reservations_verified)) else "failed"


def initialization_skip_message(state: dict) -> str:
    message = str(state.get("message") or "请先初始化账号后再运行预约").strip()
    return message if message else "请先初始化账号后再运行预约"


def parse_period_arguments(values: list[str] | None) -> dict[str, tuple[str, str]]:
    parsed = {}
    for value in values or []:
        if "=" not in value:
            raise ValueError("学习窗口格式必须是 period=HH:MM-HH:MM")
        name, window = value.split("=", 1)
        name = name.strip().lower()
        if name not in PERIOD_NAMES:
            raise ValueError(f"未知学习时段：{name}")
        parts = window.strip().split("-", 1)
        if len(parts) != 2:
            raise ValueError(f"{name} 学习窗口格式必须是 HH:MM-HH:MM")
        start, end = (_validate_clock(item, name) for item in parts)
        if _minutes(end) <= _minutes(start):
            raise ValueError(f"{name} 学习窗口结束时间必须晚于开始时间")
        parsed[name] = (start, end)
    return parsed


def seat_preference_from_input(mode: str, value: str | None = None) -> dict:
    mode = str(mode or "").strip().lower()
    if mode == "random":
        return {"mode": "random"}
    if mode == "floor":
        floor = str(value or "").strip()
        if not floor:
            raise ValueError("指定楼层不能为空")
        return {"mode": "floor", "floor": floor}
    if mode == "seats":
        seats = [item for item in str(value or "").split() if item.strip()]
        if not seats:
            raise ValueError("具体座位列表不能为空")
        return {"mode": "seats", "seats": seats}
    raise ValueError("座位偏好只能是 random、floor 或 seats")


def location_preference_from_input(library: str, floor: str = "", room: str = "") -> dict:
    library = str(library or "").strip()
    if not library:
        raise ValueError("必须选择图书馆")
    return {
        "library": library,
        "floor": str(floor or "").strip(),
        "room": str(room or "").strip(),
    }


def location_preference_from_payload(payload: dict) -> dict:
    """Validate the structured location payload used by future bot adapters."""
    if not isinstance(payload, dict):
        raise ValueError("位置偏好必须是对象")
    return location_preference_from_input(
        payload.get("library", ""),
        payload.get("floor", ""),
        payload.get("room", ""),
    )


def choose_library_from_input(libraries: list[str], value: str) -> str:
    available = [str(item).strip() for item in libraries if str(item).strip()]
    target = str(value or "").strip()
    if not target:
        raise ValueError("必须选择图书馆")
    normalized = "".join(target.split())
    for library in available:
        if "".join(library.split()) == normalized:
            return library
    raise ValueError(f"未找到图书馆：{target}")


def periods_to_config(periods: dict[str, tuple[str, str]]) -> dict:
    result = {}
    for name in PERIOD_NAMES:
        start, end = periods[name]
        result[name] = {
            "arrival_window": [start, end],
            "departure_window": [end, end],
            "default_arrival": _default_arrival(name, start),
        }
    return result


def update_account_initialization(
    path: str | Path,
    account_id: str,
    periods: dict[str, tuple[str, str]],
    seat_preference: dict,
    location_preference: dict | None = None,
) -> None:
    config_path = Path(path)
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    entries = payload.get("accounts") if isinstance(payload, dict) else payload
    if not isinstance(entries, list):
        raise ValueError("账号配置必须是 accounts 列表")
    target = next((entry for entry in entries if isinstance(entry, dict) and entry.get("id") == account_id), None)
    if target is None:
        raise ValueError(f"未找到账号：{account_id}")
    initialization = target.setdefault("initialization", {})
    if not isinstance(initialization, dict):
        raise ValueError(f"账号 {account_id} 的 initialization 必须是对象")
    initialization["periods"] = periods_to_config(periods)
    initialization["seat_preference"] = seat_preference
    if location_preference is not None:
        initialization["location_preference"] = location_preference
        initialization["library"] = location_preference["library"]
        initialization["floor"] = location_preference["floor"]
        initialization["room"] = location_preference["room"]
    if seat_preference.get("mode") == "seats":
        initialization["preferred_seats"] = list(seat_preference["seats"])
    config_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


async def run_interactive_initialization(
    account_id: str,
    settings,
    config_path: str | Path,
    verifier,
    booking_adapter=None,
    input_fn=input,
    output_fn=print,
    period_overrides: dict[str, tuple[str, str]] | None = None,
) -> dict:
    """Run read-only verification, then persist user-selected preferences.

    ``booking_adapter`` is accepted as an explicit safety seam for tests and
    callers. It is deliberately never invoked by this workflow.
    """
    repository = Repository(str(settings.db_path), account_id)
    try:
        verification = await run_initialization_verification(verifier)
    except Exception as exc:
        message = str(exc) or "只读接口验证失败"
        repository.save_initialization_state(
            status="failed",
            login_verified=False,
            home_verified=False,
            my_reservations_verified=False,
            capabilities={},
            message=message,
        )
        output_fn(f"账号 {account_id} 初始化失败：{message}")
        return repository.initialization_state()
    if not verification["ready"]:
        message = "登录、座位系统首页或我的预约接口验证失败"
        repository.save_initialization_state(
            status="failed",
            login_verified=bool(verification.get("login", False)),
            home_verified=bool(verification.get("home", False)),
            my_reservations_verified=bool(verification.get("my_reservations", False)),
            capabilities=verification.get("capabilities", {}),
            message=message,
        )
        output_fn(f"账号 {account_id} 初始化失败：{message}")
        return repository.initialization_state()

    periods = {
        name: tuple(period.arrival_window)
        for name, period in settings.periods.items()
        if name in PERIOD_NAMES
    }
    periods.update(period_overrides or {})
    for name in PERIOD_NAMES:
        current = periods[name]
        output_fn(f"{name} 学习窗口当前为 {current[0]}-{current[1]}；输入新窗口 HH:MM-HH:MM，直接回车保持：")
        answer = _read_input(input_fn, "").strip()
        if answer:
            periods[name] = parse_period_arguments([f"{name}={answer}"])[name]

    current_preference = getattr(settings, "seat_preference", None) or {}
    output_fn("座位偏好：输入 random 随机派位、floor 指定楼层、seats 指定座位列表：")
    mode = _read_input(input_fn, "").strip().lower()
    mode = {"随机": "random", "楼层": "floor", "座位": "seats"}.get(mode, mode)
    if not mode:
        mode = str(current_preference.get("mode") or "random")
    value = None
    if mode in {"floor", "楼层"}:
        value = _read_input(input_fn, "请输入楼层，例如 4F：").strip()
        mode = "floor"
    elif mode in {"seats", "座位"}:
        value = _read_input(input_fn, "请输入座位号，使用空格分隔，例如 169 168 170：").strip()
        mode = "seats"
    preference = seat_preference_from_input(mode, value)
    current_location = getattr(settings, "location_preference", None) or {}
    output_fn("位置偏好：图书馆必须填写；楼层和阅览室可以留空。")
    library_catalog = [
        str(item).strip()
        for item in verification.get("library_catalog", [])
        if str(item).strip()
    ]
    if library_catalog:
        output_fn("可选图书馆：" + "、".join(library_catalog))
    library = _read_input(input_fn, "请输入图书馆，例如 老图或新图：").strip()
    if not library:
        library = str(current_location.get("library", "")).strip()
    if library_catalog:
        library = choose_library_from_input(library_catalog, library)
    floor = _read_input(input_fn, "请输入楼层，可直接回车随机阅览室：").strip()
    if not floor:
        floor = str(current_location.get("floor", "")).strip()
    room = _read_input(input_fn, "请输入阅览室，可直接回车按规则分配：").strip()
    if not room:
        room = str(current_location.get("room", "")).strip()
    location = location_preference_from_input(library, floor, room)
    update_account_initialization(config_path, account_id, periods, preference, location)
    repository.save_initialization_state(
        status="ready",
        login_verified=True,
        home_verified=True,
        my_reservations_verified=True,
        capabilities=verification.get("capabilities", {}),
        message="初始化验证成功；未执行预约",
    )
    state = repository.initialization_state()
    output_fn(f"账号 {account_id} 初始化完成：已验证登录、座位系统首页和我的预约接口。")
    return state


def _validate_clock(value: str, name: str) -> str:
    try:
        parsed = datetime.strptime(value.strip(), "%H:%M")
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} 学习窗口时间必须是 HH:MM") from exc
    if parsed.minute % 30:
        raise ValueError(f"{name} 学习窗口时间必须按 30 分钟设置")
    return parsed.strftime("%H:%M")


def _read_input(input_fn, prompt: str) -> str:
    try:
        return input_fn(prompt)
    except TypeError:
        return input_fn()


def _minutes(value: str) -> int:
    hour, minute = map(int, value.split(":"))
    return hour * 60 + minute


def _default_arrival(name: str, start: str) -> str:
    offsets = {"morning": 30, "afternoon": 30, "evening": 30}
    return _from_minutes(_minutes(start) + offsets[name])


def _from_minutes(value: int) -> str:
    return f"{value // 60:02d}:{value % 60:02d}"
