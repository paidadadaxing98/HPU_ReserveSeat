"""Pure helpers and persistence utilities for one-time account initialization."""

import json
import re
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


def parse_time_arguments(values: list[str] | None) -> dict[str, tuple[str, str]]:
    """Parse the three positional learning windows used by ``--time``.

    ``x`` keeps the account's current value and therefore does not appear in
    the returned overrides.
    """
    values = list(values or [])
    if len(values) != len(PERIOD_NAMES):
        raise ValueError("--time 必须依次提供上午、下午、晚上三个值")
    parsed = {}
    for name, value in zip(PERIOD_NAMES, values):
        if str(value).strip().lower() in {"x", "保持", "不变"}:
            continue
        parsed[name] = parse_period_arguments([f"{name}={value}"])[name]
    return parsed


def parse_seat_rule(value: str) -> dict[str, str]:
    """Parse a CLI location rule such as ``2-9-109``.

    The three components are library index, room index, and seat number.
    ``x`` means that the component is intentionally left unconstrained.
    """
    text = str(value or "").strip().lower().replace("Ｘ", "x")
    parts = text.split("-")
    if len(parts) != 3 or any(not re.fullmatch(r"(?:x|[1-9]\d*)", part) for part in parts):
        raise ValueError(f"座位规则格式无效：{value}；应为 图书馆-阅览室-座位，例如 2-x-x")
    if parts[0] == "x":
        raise ValueError("座位规则必须指定图书馆，例如 2-x-x")
    return {"library": parts[0], "room": parts[1], "seat": parts[2]}


def sort_seat_rules(rules: list[dict]) -> list[dict]:
    """Prefer rules with more precise library/room/seat components."""
    return sorted(
        list(rules or []),
        key=lambda rule: -sum(str(rule.get(key, "x")).lower() != "x" for key in ("library", "room", "seat")),
    )


def filter_rooms_by_floor(rooms: list[str], floor: str) -> list[str]:
    target = _normalize_floor(floor)
    if not target:
        return [str(room).strip() for room in rooms if str(room).strip()]
    return [room for room in rooms if _normalize_floor(room) == target]


def resolve_seat_rule(rule: dict, libraries: list[str], rooms_by_library: dict[str, list[str]]) -> dict:
    """Resolve numeric CLI indexes into the persisted human-readable form."""
    library_index = int(rule["library"])
    available_libraries = [str(item).strip() for item in libraries if str(item).strip()]
    if not 1 <= library_index <= len(available_libraries):
        raise ValueError(f"图书馆编号无效：{library_index}；请输入 1-{len(available_libraries)}")
    library = available_libraries[library_index - 1]
    room_value = str(rule.get("room", "x")).lower()
    seat_value = str(rule.get("seat", "x")).lower()
    room = ""
    if room_value != "x":
        rooms = [str(item).strip() for item in rooms_by_library.get(library, []) if str(item).strip()]
        room_index = int(room_value)
        if not 1 <= room_index <= len(rooms):
            raise ValueError(f"图书馆‘{library}’的阅览室编号无效：{room_index}；请输入 1-{len(rooms)}")
        room = rooms[room_index - 1]
    return {
        "library": library,
        "floor": "",
        "room": room,
        "seat_preference": {"mode": "seats", "seats": [seat_value]} if seat_value != "x" else {"mode": "random"},
    }


def seat_rule_to_preferences(rule: dict) -> tuple[dict, dict]:
    location = {
        "library": str(rule.get("library", "")).strip(),
        "floor": str(rule.get("floor", "")).strip(),
        "room": str(rule.get("room", "")).strip(),
    }
    seat = str(rule.get("seat", "")).strip()
    preference = {"mode": "seats", "seats": [seat]} if seat else {"mode": "random"}
    return location, preference


def _normalize_floor(value: str) -> str:
    text = re.sub(r"\s+", "", str(value or "")).lower()
    match = re.search(r"(\d+)(?:层|楼|f)", text)
    if match:
        return f"{match.group(1)}f"
    match = re.search(r"([一二三四五六七])(?:层|楼)", text)
    return {"一": "1f", "二": "2f", "三": "3f", "四": "4f", "五": "5f", "六": "6f", "七": "7f"}.get(match.group(1), "") if match else ""


def seat_preference_from_input(mode: str, value: str | None = None) -> dict:
    mode = str(mode or "").strip().lower()
    mode = {
        "1": "random",
        "2": "floor",
        "3": "seats",
        "随机": "random",
        "随机座位": "random",
        "按楼层随机": "floor",
        "楼层随机": "floor",
        "指定座位": "seats",
        "具体座位": "seats",
    }.get(mode, mode)
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


def choose_numbered_option(
    options: list[str],
    value: str,
    label: str,
    allow_auto: bool = False,
) -> str:
    """Resolve a displayed option by 1-based number or exact text."""
    available = [str(item).strip() for item in options if str(item).strip()]
    target = str(value or "").strip()
    if allow_auto and target in {"", "0", "自动", "随机", "自动分配"}:
        return ""
    if target.isdigit():
        index = int(target)
        if 1 <= index <= len(available):
            return available[index - 1]
        if not available:
            raise ValueError(f"没有可选的{label}")
        raise ValueError(f"{label}编号无效，请输入 1-{len(available)}")
    normalized = "".join(target.split())
    for option in available:
        if "".join(option.split()) == normalized:
            return option
    raise ValueError(f"未找到{label}：{target}；请输入列表中的编号")


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
    seat_rules: list[dict] | None = None,
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
    if seat_rules is not None:
        initialization["seat_rules"] = seat_rules
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
    seat_rule_values: list[str] | None = None,
    prompt_periods: bool = True,
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

    current_location = getattr(settings, "location_preference", None) or {}
    current_preference = getattr(settings, "seat_preference", None) or {}
    library_catalog = [
        str(item).strip()
        for item in verification.get("library_catalog", [])
        if str(item).strip()
    ]
    if not library_catalog:
        message = "未读取到图书馆选项。请确认已进入座位系统首页并重新运行初始化。"
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
    catalog_errors = verification.get("catalog_errors") or {}
    if catalog_errors:
        details = "；".join(
            f"{library}：{error}"
            for library, error in catalog_errors.items()
        )
        message = f"阅览室目录采集失败，未保存初始化配置：{details}"
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
    rooms_by_library = verification.get("rooms_by_library") or {}
    if seat_rule_values:
        raw_rules = sort_seat_rules([parse_seat_rule(value) for value in seat_rule_values])
        resolved_rules = []
        for raw_rule in raw_rules:
            resolved = resolve_seat_rule(raw_rule, library_catalog, rooms_by_library)
            resolved_rules.append({
                "library": resolved["library"],
                "room": resolved["room"],
                "seat": "" if raw_rule["seat"] == "x" else raw_rule["seat"],
                "library_index": int(raw_rule["library"]),
                "room_index": None if raw_rule["room"] == "x" else int(raw_rule["room"]),
            })
        primary = resolve_seat_rule(raw_rules[0], library_catalog, rooms_by_library)
        library, floor, room, preference = (
            primary["library"], primary["floor"], primary["room"], primary["seat_preference"]
        )
    else:
        output_fn("请选择座位偏好：")
        output_fn("  1. 随机空闲座位：只选择图书馆，其余自动随机")
        output_fn("  2. 指定楼层内随机空闲座位：选择图书馆和楼层")
        output_fn("  3. 指定具体座位优先：选择图书馆、阅览室和座位号")
        output_fn("直接回车保持当前设置；新账号直接回车使用 1。")
        mode_input = _read_input(input_fn, "请输入座位偏好编号：").strip().lower()
        preserve_preference = not mode_input and bool(current_preference)
        mode = str(current_preference.get("mode") or "1") if preserve_preference else (mode_input or "1")
        normalized_mode = {"1": "random", "2": "floor", "3": "seats"}.get(mode, mode)
        if normalized_mode not in {"random", "floor", "seats"}:
            raise ValueError("座位偏好只能输入 1、2 或 3")
        preference = dict(current_preference) if preserve_preference else {"mode": normalized_mode}

        output_fn("请选择图书馆：")
        for index, name in enumerate(library_catalog, 1):
            output_fn(f"  {index}. {name}")
        library = _read_valid_option(
            input_fn, output_fn, "请输入图书馆编号（也可输入完整名称）：",
            lambda value: choose_numbered_option(
                library_catalog,
                value or str(current_location.get("library", "")).strip(),
                "图书馆",
            ),
        )
        room_catalog = [str(item).strip() for item in rooms_by_library.get(library, []) if str(item).strip()]
        floor = str(current_location.get("floor", "")).strip() if preserve_preference else ""
        room = str(current_location.get("room", "")).strip() if preserve_preference else ""

        if normalized_mode == "random":
            floor = ""
            room = ""
            preference = {"mode": "random"}
        elif normalized_mode == "floor":
            floor = _read_valid_option(
                input_fn, output_fn,
                "请输入座位楼层，例如 4F（直接回车保持当前楼层）：",
                lambda value: (value or floor).strip() or (_raise_value("指定楼层不能为空")),
            )
            filtered_rooms = filter_rooms_by_floor(room_catalog, floor)
            output_fn(f"{library} 的 {floor} 可选阅览室：")
            for index, name in enumerate(filtered_rooms, 1):
                output_fn(f"  {index}. {name}")
            if not filtered_rooms:
                output_fn("该楼层暂未读取到阅览室，将在预约时重新读取。")
            else:
                output_fn("阅览室由调度器按账号独立轮询，从该层第一个阅览室开始分配；无需再次选择。")
            room = ""
            preference = {"mode": "floor", "floor": floor}
        else:
            if not room_catalog:
                raise ValueError(f"图书馆‘{library}’暂未读取到阅览室，无法设置具体座位偏好")
            output_fn(f"{library} 可选阅览室：")
            for index, name in enumerate(room_catalog, 1):
                output_fn(f"  {index}. {name}")
            room = _read_valid_option(
                input_fn, output_fn,
                "请输入阅览室编号（必须选择具体阅览室）：",
                lambda value: choose_numbered_option(room_catalog, value or room, "阅览室"),
            )
            seats = _read_valid_option(
                input_fn, output_fn,
                "请输入座位号，使用空格分隔，例如 169 168 170：",
                lambda value: seat_preference_from_input("seats", value or " ".join(preference.get("seats", []))),
            )
            preference = seats
            floor = ""
        resolved_rules = None

    if prompt_periods:
        for name in PERIOD_NAMES:
            current = periods[name]
            output_fn(f"{name} 学习窗口当前为 {current[0]}-{current[1]}。")
            output_fn("直接回车保持当前窗口，否则输入 HH:MM-HH:MM，例如 08:00-12:00：")
            while True:
                answer = _read_input(input_fn, "").strip()
                if not answer:
                    break
                try:
                    periods[name] = parse_period_arguments([f"{name}={answer}"])[name]
                    break
                except ValueError as exc:
                    output_fn(f"输入无效：{exc}请重新输入。")
    location = location_preference_from_input(library, floor, room)
    update_account_initialization(config_path, account_id, periods, preference, location, resolved_rules)
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


def _raise_value(message: str):
    raise ValueError(message)


def _read_valid_option(input_fn, output_fn, prompt: str, resolver):
    while True:
        value = _read_input(input_fn, prompt).strip()
        try:
            return resolver(value)
        except (TypeError, ValueError) as exc:
            output_fn(f"输入无效：{exc}")


def _minutes(value: str) -> int:
    hour, minute = map(int, value.split(":"))
    return hour * 60 + minute


def _default_arrival(name: str, start: str) -> str:
    offsets = {"morning": 30, "afternoon": 30, "evening": 30}
    return _from_minutes(_minutes(start) + offsets[name])


def _from_minutes(value: int) -> str:
    return f"{value // 60:02d}:{value % 60:02d}"
