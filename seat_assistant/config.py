from dataclasses import dataclass, field
import json
import os
from pathlib import Path

from .initialization import parse_seat_rule, sort_seat_rules


MAX_ACCOUNTS = 20


def _load_dotenv(path: str = ".env") -> None:
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass
class Period:
    arrival_window: tuple[str, str]
    departure_window: tuple[str, str]
    default_arrival: str
    enabled: bool = True


def _default_periods() -> dict[str, Period]:
    return {
        "morning": Period(("08:00", "12:00"), ("12:00", "12:00"), "08:30"),
        "afternoon": Period(("14:30", "18:30"), ("18:30", "18:30"), "15:00"),
        "evening": Period(("19:30", "22:00"), ("22:00", "22:00"), "20:00"),
        "period04": Period(("10:00", "12:00"), ("12:00", "12:00"), "10:30", False),
        "period05": Period(("13:00", "15:00"), ("15:00", "15:00"), "13:30", False),
    }


@dataclass
class Settings:
    account_id: str = "default"
    account: str = ""
    password: str = ""
    control_token: str = field(default_factory=lambda: os.getenv("SEAT_CONTROL_TOKEN", "change-me"))
    dry_run: bool = True
    db_path: str = "seat_assistant.db"
    control_host: str = "127.0.0.1"
    wecom_webhook: str = ""
    wecom_bot_id: str = ""
    wecom_bot_secret: str = ""
    wecom_bot_ws_url: str = "wss://openws.work.weixin.qq.com"
    wecom_bot_default_user: str = ""
    wecom_bot_lock_file: str = "logs/wecom-bot.lock"
    wecom_aliases: tuple[str, ...] = ()
    notify_reservation_results: bool = True
    login_url: str = "https://seatlib.hpu.edu.cn/libseat/"
    max_reservations_per_run: int = 1
    daily_success_limit: int = 5
    account_interval_seconds: float = 15.0
    captcha_llm_enabled: bool = False
    captcha_llm_api_key: str = ""
    captcha_llm_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    captcha_llm_model: str = "qwen3.7-flash"
    captcha_llm_timeout_seconds: float = 15.0
    captcha_llm_max_attempts: int = 2
    profile_path: str = ".browser-profile"
    periods: dict[str, Period] = field(default_factory=_default_periods)
    preferred_seats: tuple[str, ...] = ()
    seat_preference: dict = field(default_factory=dict)
    seat_rules: list[dict] = field(default_factory=list)
    location_preference: dict = field(default_factory=dict)
    require_initialization: bool = False

    def __post_init__(self):
        if not self.control_token.strip():
            raise ValueError("control_token cannot be blank")
        if self.max_reservations_per_run != 1:
            raise ValueError("max_reservations_per_run 只能为 1")
        if not 1 <= self.daily_success_limit <= 5:
            raise ValueError("daily_success_limit 必须在 1 到 5 之间")
        if self.account_interval_seconds < 0:
            raise ValueError("account_interval_seconds 不能小于 0")
        if not self.captcha_llm_base_url.startswith(("http://", "https://")):
            raise ValueError("验证码模型 Base URL 必须是 http:// 或 https:// 地址")
        if not self.captcha_llm_model.strip():
            raise ValueError("验证码模型名称不能为空")
        if self.captcha_llm_timeout_seconds <= 0:
            raise ValueError("验证码模型超时时间必须大于 0")
        if not 1 <= self.captcha_llm_max_attempts <= 3:
            raise ValueError("验证码模型最多尝试次数必须在 1 到 3 之间")
        if self.captcha_llm_enabled and not self.captcha_llm_api_key.strip():
            raise ValueError("验证码模型已启用但 API Key 为空")


@dataclass(frozen=True)
class AccountSettings:
    id: str
    account: str
    password: str
    profile_path: Path
    db_path: Path
    wecom_webhook: str = ""
    wecom_user_id: str = ""
    wecom_aliases: tuple[str, ...] = ()
    login_url: str = "https://seatlib.hpu.edu.cn/libseat/"
    periods: dict[str, Period] = field(default_factory=_default_periods)
    preferred_seats: tuple[str, ...] = ()
    seat_preference: dict = field(default_factory=dict)
    seat_rules: list[dict] = field(default_factory=list)
    location_preference: dict = field(default_factory=dict)


def _copy_periods(periods: dict[str, Period]) -> dict[str, Period]:
    return {
        name: Period(
            tuple(period.arrival_window),
            tuple(period.departure_window),
            period.default_arrival,
            bool(getattr(period, "enabled", True)),
        )
        for name, period in periods.items()
    }


def _normalize_seat_preference(raw, legacy: tuple[str, ...] = (), account_id: str = "<空>") -> dict:
    if raw is None:
        return {"mode": "seats", "seats": list(legacy)} if legacy else {}
    if not isinstance(raw, dict):
        raise ValueError(f"账号 {account_id} 的 seat_preference 必须是对象")
    mode = str(raw.get("mode", "")).strip().lower()
    if mode not in {"random", "floor", "seats"}:
        raise ValueError(f"账号 {account_id} 的 seat_preference.mode 必须是 random、floor 或 seats")
    if mode == "random":
        return {"mode": "random"}
    if mode == "floor":
        floor = str(raw.get("floor", "")).strip()
        if not floor:
            raise ValueError(f"账号 {account_id} 的楼层偏好不能为空")
        return {"mode": "floor", "floor": floor}
    values = raw.get("seats", legacy)
    if not isinstance(values, (list, tuple)):
        raise ValueError(f"账号 {account_id} 的座位偏好必须是列表")
    seats = [str(value).strip() for value in values if str(value).strip()]
    if not seats:
        raise ValueError(f"账号 {account_id} 的具体座位列表不能为空")
    return {"mode": "seats", "seats": seats}


def _normalize_location_preference(raw, account_id: str = "<空>") -> dict:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError(f"账号 {account_id} 的位置偏好必须是对象")
    library = str(raw.get("library", "")).strip()
    if not library:
        raise ValueError(f"账号 {account_id} 的位置偏好必须选择图书馆")
    floor = str(raw.get("floor", "")).strip()
    room = str(raw.get("room", "")).strip()
    return {"library": library, "floor": floor, "room": room}


def _normalize_seat_rules(raw, account_id: str = "<空>") -> list[dict]:
    if raw is None:
        return []
    if not isinstance(raw, (list, tuple)):
        raise ValueError(f"账号 {account_id} 的 seat_rules 必须是列表")
    rules = []
    for value in raw:
        try:
            if isinstance(value, str):
                rules.append(parse_seat_rule(value))
                continue
            if not isinstance(value, dict):
                raise ValueError("规则必须是字符串或对象")
            library = str(value.get("library", "")).strip()
            room = str(value.get("room", "")).strip()
            seat = str(value.get("seat", "")).strip()
            if not library:
                raise ValueError("规则必须指定图书馆")
            if library.isdigit() and room in {"", "x"} and seat in {"", "x"}:
                rules.append(parse_seat_rule(f"{library}-x-x"))
            elif library.isdigit() and room.isdigit() and seat in {"", "x"}:
                rules.append(parse_seat_rule(f"{library}-{room}-x"))
            elif library.isdigit() and room.isdigit() and seat.isdigit():
                rules.append(parse_seat_rule(f"{library}-{room}-{seat}"))
            else:
                rules.append({
                    "library": library,
                    "room": "x" if room in {"", "x"} else room,
                    "seat": "x" if seat in {"", "x"} else seat,
                    **({"library_index": value["library_index"]} if "library_index" in value else {}),
                    **({"room_index": value["room_index"]} if "room_index" in value else {}),
                })
        except (AttributeError, TypeError, ValueError) as exc:
            raise ValueError(f"账号 {account_id} 的座位规则无效：{value}") from exc
    return sort_seat_rules(rules)


def _parse_initialization(entry: dict, inherited: dict | None = None) -> tuple[dict[str, Period], tuple[str, ...], dict, list[dict], dict]:
    source = inherited or {
        "periods": _default_periods(),
        "preferred_seats": (),
        "seat_preference": {},
        "seat_rules": [],
        "location_preference": {},
    }
    periods = _copy_periods(source["periods"])
    preferred = tuple(str(value).strip() for value in source["preferred_seats"] if str(value).strip())
    seat_preference = dict(source.get("seat_preference") or {})
    seat_rules = list(source.get("seat_rules") or [])
    location_preference = dict(source.get("location_preference") or {})
    initialization = entry.get("initialization") or {}
    if not isinstance(initialization, dict):
        raise ValueError(f"账号 {entry.get('id', '<空>')} 的 initialization 必须是对象")
    raw_seats = initialization.get("preferred_seats")
    if raw_seats is not None:
        if not isinstance(raw_seats, list):
            raise ValueError(f"账号 {entry.get('id', '<空>')} 的 preferred_seats 必须是列表")
        preferred = tuple(str(value).strip() for value in raw_seats if str(value).strip())
        seat_preference = {"mode": "seats", "seats": list(preferred)} if preferred else {}
    if "seat_preference" in initialization:
        seat_preference = _normalize_seat_preference(initialization.get("seat_preference"), preferred, str(entry.get("id", "<空>")))
        if seat_preference.get("mode") == "seats":
            preferred = tuple(seat_preference["seats"])
    if "seat_rules" in initialization:
        seat_rules = _normalize_seat_rules(initialization.get("seat_rules"), str(entry.get("id", "<空>")))
    location_value = initialization.get("location_preference")
    if location_value is None and any(key in initialization for key in ("library", "floor", "room")):
        location_value = {
            "library": initialization.get("library", ""),
            "floor": initialization.get("floor", ""),
            "room": initialization.get("room", ""),
        }
    if location_value is not None:
        location_preference = _normalize_location_preference(location_value, str(entry.get("id", "<空>")))
    raw_periods = initialization.get("periods") or {}
    if not isinstance(raw_periods, dict):
        raise ValueError(f"账号 {entry.get('id', '<空>')} 的 periods 必须是对象")
    for name, value in raw_periods.items():
        if name not in periods or not isinstance(value, dict):
            raise ValueError(f"账号 {entry.get('id', '<空>')} 的时段配置无效：{name}")
        current = periods[name]
        arrival = tuple(str(item).strip() for item in value.get("arrival_window", current.arrival_window))
        departure = tuple(str(item).strip() for item in value.get("departure_window", current.departure_window))
        default = str(value.get("default_arrival", current.default_arrival)).strip()
        if len(arrival) != 2 or len(departure) != 2 or not default:
            raise ValueError(f"账号 {entry.get('id', '<空>')} 的时段配置无效：{name}")
        enabled = value.get("enabled", current.enabled)
        if not isinstance(enabled, bool):
            raise ValueError(f"账号 {entry.get('id', '<空>')} 的时段 enabled 必须是 true 或 false：{name}")
        periods[name] = Period(arrival, departure, default, enabled)
    return periods, preferred, seat_preference, seat_rules, location_preference


def _resolve_initialization(entry: dict, by_id: dict[str, dict], trail: tuple[str, ...] = ()) -> tuple[dict[str, Period], tuple[str, ...], dict]:
    account_id = str(entry.get("id", ""))
    if account_id in trail:
        raise ValueError(f"账号初始化继承出现循环：{' -> '.join((*trail, account_id))}")
    initialization = entry.get("initialization") or {}
    parent_id = initialization.get("inherits_from") if isinstance(initialization, dict) else None
    inherited = None
    if parent_id:
        parent = by_id.get(str(parent_id))
        if parent is None:
            raise ValueError(f"账号 {account_id} 继承的模板账号不存在：{parent_id}")
        parent_periods, parent_seats, parent_preference, parent_rules, parent_location = _resolve_initialization(parent, by_id, (*trail, account_id))
        inherited = {
            "periods": parent_periods,
            "preferred_seats": parent_seats,
            "seat_preference": parent_preference,
            "seat_rules": parent_rules,
            "location_preference": parent_location,
        }
    return _parse_initialization(entry, inherited)


def load_accounts(path: str | None = None) -> list[AccountSettings]:
    """Load isolated account credentials, falling back to the legacy .env account."""
    config_path = Path(path or os.getenv("SEAT_ACCOUNTS_FILE", "accounts.json")).resolve()
    if not config_path.exists():
        return [AccountSettings(
            id="default",
            account=os.getenv("SEAT_ACCOUNT", "").strip(),
            password=os.getenv("SEAT_PASSWORD", "").strip(),
            profile_path=(Path(".browser-profile")).resolve(),
            db_path=Path(os.getenv("SEAT_DB_PATH", "seat_assistant.db")).resolve(),
            wecom_webhook=os.getenv("SEAT_WECOM_WEBHOOK", "").strip(),
            wecom_user_id=os.getenv("SEAT_WECOM_BOT_DEFAULT_USER", "").strip(),
            login_url=os.getenv("SEAT_LOGIN_URL", "https://seatlib.hpu.edu.cn/libseat/"),
            location_preference={
                "library": "南校区第二图书馆",
                "floor": "",
                "room": "",
            },
        )]
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"账号配置文件无法读取：{config_path}") from exc
    entries = raw.get("accounts") if isinstance(raw, dict) else raw
    if not isinstance(entries, list):
        raise ValueError("账号配置必须是 accounts 列表")
    if not entries:
        raise ValueError("至少配置一个账号")
    if len(entries) > MAX_ACCOUNTS:
        raise ValueError(f"最多支持 {MAX_ACCOUNTS} 个账号")
    root = config_path.parent
    entries_by_id = {str(item.get("id", "")).strip(): item for item in entries if isinstance(item, dict)}
    accounts = []
    ids = set()
    credentials = set()
    profile_paths = set()
    database_paths = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("每个账号配置必须是对象")
        account_id = str(entry.get("id", "")).strip()
        account = str(entry.get("account", "")).strip()
        password = str(entry.get("password", "")).strip()
        enabled = entry.get("enabled", True)
        if not isinstance(enabled, bool):
            raise ValueError(f"账号 {account_id or '<空>'} 的 enabled 必须是 true 或 false")
        if not account_id:
            raise ValueError("账号 ID 不能为空")
        if account_id in {".", ".."} or Path(account_id).name != account_id:
            raise ValueError(f"账号 ID 不是安全目录名：{account_id}")
        if not enabled:
            if account_id in ids:
                raise ValueError(f"账号 ID 重复：{account_id}")
            ids.add(account_id)
            continue
        if not account or not password:
            raise ValueError("账号 ID、账号和密码不能为空")
        if account_id in ids:
            raise ValueError(f"账号 ID 重复：{account_id}")
        if account in credentials:
            raise ValueError(f"学号重复：{account}")
        ids.add(account_id)
        credentials.add(account)
        profile = Path(entry.get("profile_path") or root / "accounts" / account_id / "browser-profile")
        database = Path(entry.get("db_path") or root / "accounts" / account_id / "seat_assistant.db")
        if not profile.is_absolute():
            profile = root / profile
        if not database.is_absolute():
            database = root / database
        profile = profile.resolve()
        database = database.resolve()
        if profile in profile_paths:
            raise ValueError(f"浏览器会话目录重复：{profile}")
        if database in database_paths:
            raise ValueError(f"数据库路径重复：{database}")
        profile_paths.add(profile)
        database_paths.add(database)
        periods, preferred_seats, seat_preference, seat_rules, location_preference = _resolve_initialization(entry, entries_by_id)
        accounts.append(AccountSettings(
            id=account_id,
            account=account,
            password=password,
            profile_path=profile,
            db_path=database,
            wecom_webhook=str(entry.get("wecom_webhook", "")).strip(),
            wecom_user_id=str(entry.get("wecom_user_id", "")).strip(),
            wecom_aliases=tuple(
                str(value).strip()
                for value in (entry.get("wecom_aliases") or [])
                if str(value).strip()
            ),
            login_url=str(entry.get("login_url") or os.getenv("SEAT_LOGIN_URL", "https://seatlib.hpu.edu.cn/libseat/")).strip(),
            periods=periods,
            preferred_seats=preferred_seats,
            seat_preference=seat_preference,
            seat_rules=seat_rules,
            location_preference=location_preference,
        ))
    return accounts


def load_settings() -> Settings:
    _load_dotenv()
    return Settings(
        account=os.getenv("SEAT_ACCOUNT", ""),
        password=os.getenv("SEAT_PASSWORD", ""),
        control_token=os.getenv("SEAT_CONTROL_TOKEN", "change-me"),
        dry_run=os.getenv("SEAT_DRY_RUN", "true").lower() != "false",
        db_path=os.getenv("SEAT_DB_PATH", "seat_assistant.db"),
        control_host=os.getenv("SEAT_CONTROL_HOST", "127.0.0.1"),
        wecom_webhook=os.getenv("SEAT_WECOM_WEBHOOK", ""),
        wecom_bot_id=os.getenv("SEAT_WECOM_BOT_ID", "").strip(),
        wecom_bot_secret=os.getenv("SEAT_WECOM_BOT_SECRET", "").strip(),
        wecom_bot_ws_url=os.getenv("SEAT_WECOM_BOT_WS_URL", "wss://openws.work.weixin.qq.com").strip(),
        wecom_bot_default_user=os.getenv("SEAT_WECOM_BOT_DEFAULT_USER", "").strip(),
        wecom_bot_lock_file=os.getenv("SEAT_WECOM_BOT_LOCK_FILE", "logs/wecom-bot.lock").strip(),
        login_url=os.getenv("SEAT_LOGIN_URL", "https://seatlib.hpu.edu.cn/libseat/"),
        max_reservations_per_run=int(os.getenv("SEAT_MAX_RESERVATIONS_PER_RUN", "1")),
        daily_success_limit=int(os.getenv("SEAT_DAILY_SUCCESS_LIMIT", "5")),
        account_interval_seconds=float(os.getenv("SEAT_ACCOUNT_INTERVAL_SECONDS", "15")),
        captcha_llm_enabled=os.getenv("SEAT_CAPTCHA_LLM_ENABLED", "false").lower() in {"1", "true", "yes", "on"},
        captcha_llm_api_key=os.getenv("SEAT_CAPTCHA_LLM_API_KEY", "").strip(),
        captcha_llm_base_url=os.getenv("SEAT_CAPTCHA_LLM_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1").strip().rstrip("/"),
        captcha_llm_model=os.getenv("SEAT_CAPTCHA_LLM_MODEL", "qwen3.7-flash").strip(),
        captcha_llm_timeout_seconds=float(os.getenv("SEAT_CAPTCHA_LLM_TIMEOUT_SECONDS", "15")),
        captcha_llm_max_attempts=int(os.getenv("SEAT_CAPTCHA_LLM_MAX_ATTEMPTS", "2")),
    )


def load_account_settings(account_id: str | None = None) -> Settings:
    base = load_settings()
    config_path = Path(os.getenv("SEAT_ACCOUNTS_FILE", "accounts.json")).resolve()
    accounts = load_accounts(str(config_path))
    if account_id is None:
        if len(accounts) != 1:
            raise ValueError("配置了多个账号，请使用 --account 指定账号 ID")
        selected = accounts[0]
    else:
        selected = next((item for item in accounts if item.id == account_id), None)
    if selected is None:
        raise ValueError(f"未找到账号：{account_id}")
    return Settings(
        account_id=selected.id,
        account=selected.account,
        password=selected.password,
        control_token=base.control_token,
        dry_run=base.dry_run,
        db_path=str(selected.db_path),
        control_host=base.control_host,
        wecom_webhook=selected.wecom_webhook or base.wecom_webhook,
        wecom_bot_id=base.wecom_bot_id,
        wecom_bot_secret=base.wecom_bot_secret,
        wecom_bot_ws_url=base.wecom_bot_ws_url,
        wecom_bot_default_user=base.wecom_bot_default_user,
        wecom_bot_lock_file=base.wecom_bot_lock_file,
        wecom_aliases=selected.wecom_aliases,
        login_url=selected.login_url,
        max_reservations_per_run=base.max_reservations_per_run,
        daily_success_limit=base.daily_success_limit,
        account_interval_seconds=base.account_interval_seconds,
        captcha_llm_enabled=base.captcha_llm_enabled,
        captcha_llm_api_key=base.captcha_llm_api_key,
        captcha_llm_base_url=base.captcha_llm_base_url,
        captcha_llm_model=base.captcha_llm_model,
        captcha_llm_timeout_seconds=base.captcha_llm_timeout_seconds,
        captcha_llm_max_attempts=base.captcha_llm_max_attempts,
        profile_path=str(selected.profile_path),
        periods=_copy_periods(selected.periods),
        preferred_seats=selected.preferred_seats,
        seat_preference=dict(selected.seat_preference),
        seat_rules=[dict(rule) for rule in selected.seat_rules],
        location_preference=dict(selected.location_preference),
        require_initialization=config_path.exists(),
    )
