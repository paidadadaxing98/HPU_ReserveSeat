from seat_assistant.reservation import PlaywrightReservation, SeatResult
from seat_assistant.seat_inventory import Seat, candidates_for_preference


def test_seat_preference_orders_explicit_seats_and_filters_floor():
    seats = [
        Seat("001", True, 1, "1F"),
        Seat("169", True, 169, "4F"),
        Seat("170", True, 170, "4F"),
    ]

    assert [seat.number for seat in candidates_for_preference(
        seats, {"mode": "seats", "seats": ["170", "169"]}
    )] == ["170", "169", "001"]
    assert all(seat.floor == "4F" for seat in candidates_for_preference(
        seats, {"mode": "floor", "floor": "4F"}
    ))


def test_real_adapter_stays_uncertain_without_browser_executor():
    result = PlaywrightReservation().reserve("2026-08-22", "morning", "08:30", "12:00")

    assert isinstance(result, SeatResult)
    assert result.success is False
    assert result.conclusive is False


def test_real_adapter_delegates_to_async_booking_runner():
    async def runner(settings, day, period, start, end):
        assert settings.account_id == "alice"
        return SeatResult(True, "阅览室", "169", "核验成功")

    settings = type("Settings", (), {"account_id": "alice"})()
    result = PlaywrightReservation(settings=settings, runner=runner).reserve(
        "2026-08-22", "morning", "08:30", "12:00"
    )

    assert result.success is True
    assert result.seat == "169"
