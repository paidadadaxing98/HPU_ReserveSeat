import json
import threading
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer

from seat_assistant.api import make_handler
from seat_assistant.config import Settings
from seat_assistant.reservation import DryRunReservation
from seat_assistant.service import AssistantService
from seat_assistant.storage import Repository


def _request(server, method, path, body=None, token=None):
    connection = HTTPConnection("127.0.0.1", server.server_port, timeout=3)
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if body is not None:
        headers["Content-Type"] = "application/json; charset=utf-8"
    payload = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    connection.request(method, path, payload, headers)
    response = connection.getresponse()
    payload = json.loads(response.read())
    connection.close()
    return response.status, payload


def _server(tmp_path):
    settings = Settings(control_token="local-token")
    service = AssistantService(settings, Repository(str(tmp_path / "assistant.sqlite")), DryRunReservation())
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(service, settings.control_token))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def test_health_is_public_and_status_requires_bearer_token(tmp_path):
    server, thread = _server(tmp_path)
    try:
        assert _request(server, "GET", "/api/v1/health")[0] == 200
        status, body = _request(server, "GET", "/api/v1/status")
        assert status == 401
        assert body["ok"] is False
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_command_submission_is_idempotent_by_request_id(tmp_path):
    server, thread = _server(tmp_path)
    try:
        request = {"request_id": "req-1", "command": "状态", "date": "2026-08-21"}
        status, first = _request(server, "POST", "/api/v1/commands", request, "local-token")
        assert status == 200
        assert first["ok"] is True
        assert first["request_id"] == "req-1"
        assert first["data"]["date"] == "2026-08-21"

        status, replay = _request(server, "POST", "/api/v1/commands", request, "local-token")
        assert status == 200
        assert replay["ok"] is True
        assert replay["replayed"] is True
        assert replay["data"] == first["data"]
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_command_api_rejects_missing_command_and_unknown_route(tmp_path):
    server, thread = _server(tmp_path)
    try:
        status, body = _request(server, "POST", "/api/v1/commands", {"request_id": "req-2"}, "local-token")
        assert status == 422
        assert body["error"]["code"] == "missing_command"

        status, body = _request(server, "POST", "/api/v1/commands", {"request_id": "req-3", "command": "随便说"}, "local-token")
        assert status == 422
        assert body["error"]["code"] == "invalid_command"
    finally:
        server.shutdown()
        thread.join(timeout=2)
