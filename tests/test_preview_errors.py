from seat_assistant.preview import layout_from_response


def test_layout_from_response_explains_missing_layout():
    try:
        layout_from_response({"status": False, "code": 404, "message": "room not found", "data": None})
    except ValueError as exc:
        assert "room not found" in str(exc)
    else:
        raise AssertionError("expected a readable layout error")
