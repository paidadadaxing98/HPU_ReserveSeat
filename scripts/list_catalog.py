"""List all libraries and their current room numbers without changing config."""

import argparse
import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from seat_assistant.config import _load_dotenv, load_account_settings
from scripts.initialize_account import ReadOnlyAccountVerifier


async def run(account_id: str | None) -> int:
    _load_dotenv()
    config_path = Path(os.getenv("SEAT_ACCOUNTS_FILE", "accounts.json")).resolve()
    if not config_path.exists():
        raise ValueError("未找到 accounts.json；目录采集命令需要使用多账号配置文件")
    settings = load_account_settings(account_id)
    verification = await ReadOnlyAccountVerifier(settings).verify()
    print("目录采集完成：未修改账号配置，也没有预约任何座位。")
    return 0


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="只读取当前图书馆和阅览室目录，不修改配置、不预约",
    )
    parser.add_argument(
        "--account",
        help="accounts.json 中的账号 id；多账号时必须填写",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(run(parse_args().account)))
    except KeyboardInterrupt:
        raise SystemExit(130)
    except Exception as exc:
        print(f"目录采集失败：{exc}")
        raise SystemExit(1)
