from dataclasses import dataclass, field
import json
import os
from pathlib import Path


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
    login_url: str = "https://seatlib.hpu.edu.cn/libseat/"
    max_reservations_per_run: int = 1
    daily_success_limit: int = 3
    account_interval_seconds: float = 15.0
    captcha_llm_enabled: bool = False
    captcha_llm_api_key: str = ""
    captcha_llm_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    captcha_llm_model: str = "qwen3.7-flash"
    captcha_llm_timeout_seconds: float = 15.0
    captcha_llm_max_attempts: int = 2
    profile_path: str = ".browser-profile"
    periods: dict[str, Period] = field(default_factory=lambda: {
        "morning": Period(("08:30", "09:30"), ("11:30", "13:00"), "08:55"),
        "afternoon": Period(("14:00", "15:00"), ("17:30", "19:30"), "14:20"),
        "evening": Period(("20:00", "20:30"), ("22:00", "22:00"), "20:10"),
    })

    def __post_init__(self):
        if not self.control_token.strip():
            raise ValueError("control_token cannot be blank")
        if self.max_reservations_per_run != 1:
            raise ValueError("max_reservations_per_run 只能为 1")
        if not 1 <= self.daily_success_limit <= 3:
            raise ValueError("daily_success_limit 必须在 1 到 3 之间")
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
    login_url: str = "https://seatlib.hpu.edu.cn/libseat/"


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
            login_url=os.getenv("SEAT_LOGIN_URL", "https://seatlib.hpu.edu.cn/libseat/"),
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
            if account or password:
                raise ValueError(f"未启用账号 {account_id} 的账号和密码必须同时为空")
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
        accounts.append(AccountSettings(
            id=account_id,
            account=account,
            password=password,
            profile_path=profile,
            db_path=database,
            wecom_webhook=str(entry.get("wecom_webhook", "")).strip(),
            login_url=str(entry.get("login_url") or os.getenv("SEAT_LOGIN_URL", "https://seatlib.hpu.edu.cn/libseat/")).strip(),
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
        login_url=os.getenv("SEAT_LOGIN_URL", "https://seatlib.hpu.edu.cn/libseat/"),
        max_reservations_per_run=int(os.getenv("SEAT_MAX_RESERVATIONS_PER_RUN", "1")),
        daily_success_limit=int(os.getenv("SEAT_DAILY_SUCCESS_LIMIT", "3")),
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
    accounts = load_accounts()
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
    )
