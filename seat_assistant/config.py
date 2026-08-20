from dataclasses import dataclass, field
import os


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
    account: str = ""
    password: str = ""
    control_token: str = field(default_factory=lambda: os.getenv("SEAT_CONTROL_TOKEN", "change-me"))
    dry_run: bool = True
    db_path: str = "seat_assistant.db"
    control_host: str = "127.0.0.1"
    wecom_webhook: str = ""
    login_url: str = "https://seatlib.hpu.edu.cn/libseat/"
    max_reservations_per_run: int = 1
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
    )
