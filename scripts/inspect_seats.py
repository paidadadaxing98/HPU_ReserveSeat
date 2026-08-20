import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from seat_assistant.seat_inventory import available_seats, seats_from_layout, seats_from_snapshot


def main():
    path = Path(sys.argv[1] if len(sys.argv) > 1 else "site-calibration.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    snapshots = data["snapshots"]
    bodies = data.get("response_bodies", {})
    layout_responses = [value.get("data") for url, value in bodies.items() if "/rest/v2/room/layoutByDate/" in url and isinstance(value, dict) and isinstance(value.get("data"), dict)]
    if layout_responses:
        seats = seats_from_layout(layout_responses[-1])
        confirmed = available_seats(seats)
        print(f"接口识别座位：{len(seats)}")
        print(f"接口明确空闲：{len(confirmed)}")
        print(f"接口明确不可用：{len(seats) - len(confirmed)}")
        print("首个空闲座位：" + (confirmed[0].number if confirmed else "无"))
        return
    snapshot = snapshots.get("seat_map") or snapshots.get("free_seat_map") or snapshots.get("occupied_seat_map")
    if snapshot is None:
        raise SystemExit("校准文件中没有座位图快照")
    seats = seats_from_snapshot(snapshot.get("interactive_candidates", []))
    confirmed = available_seats(seats)
    unknown = [seat.number for seat in seats if seat.available is None]
    print(f"识别座位：{len(seats)}")
    print(f"明确空闲：{len(confirmed)}")
    print(f"状态未知：{len(unknown)}")
    if not confirmed:
        print("当前校准资料没有明确空闲状态，程序不会自动选座或提交预约。")


if __name__ == "__main__":
    main()
