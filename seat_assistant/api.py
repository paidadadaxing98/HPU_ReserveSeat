from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
import json

from .commands import parse_command


def make_handler(service, token):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _authorized(self):
            authorization = self.headers.get("Authorization", "")
            bearer = authorization[7:] if authorization.startswith("Bearer ") else ""
            return (
                self.headers.get("X-Control-Token") == token
                or bearer == token
                or parse_qs(urlparse(self.path).query).get("token", [None])[0] == token
            )

        def _send(self, data, status=200, headers=None):
            raw = json.dumps(data, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Connection", "close")
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(raw)

        def _api_response(self, request_id, data=None, message="", error=None, status=200, replayed=False):
            body = {"ok": error is None, "request_id": request_id}
            if message:
                body["message"] = message
            if data is not None:
                body["data"] = data
            if error is not None:
                body["error"] = error
            if replayed:
                body["replayed"] = True
            return self._send(body, status)

        def _request_id(self, payload=None):
            if payload and payload.get("request_id"):
                return str(payload["request_id"])
            return self.headers.get("X-Request-Id", "")

        def _read_json(self):
            length = int(self.headers.get("Content-Length", 0))
            try:
                return json.loads(self.rfile.read(length) or b"{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                return None

        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/api/v1/health":
                return self._api_response("", data={"service": "seat-assistant", "status": "ok"})
            if not self._authorized():
                return self._api_response("", error={"code": "unauthorized", "message": "令牌无效"}, status=401)
            if path == "/api/v1/status":
                day = parse_qs(urlparse(self.path).query).get("date", [None])[0]
                result = service.apply_command(parse_command("状态"), day)
                return self._api_response("", data={"date": day, "reservations": result.get("reservations", [])})
            if "text/html" in self.headers.get("Accept", ""):
                body = """<!doctype html><meta name='viewport' content='width=device-width,initial-scale=1'><title>座位助手</title><h1>座位助手</h1><pre id='status'>加载中</pre><input id='command' placeholder='例如：上午推迟到 09:20'><button id='send' onclick='send()'>发送</button><script>const token=new URLSearchParams(location.search).get('token'); async function load(){try{const r=await fetch(location.pathname+'?token='+encodeURIComponent(token),{headers:{Accept:'application/json'}}); document.querySelector('#status').textContent=JSON.stringify(await r.json(),null,2)}catch(e){document.querySelector('#status').textContent='连接失败：'+e}} async function send(){const button=document.querySelector('#send'); const status=document.querySelector('#status'); button.disabled=true; status.textContent='发送中...'; try{const r=await fetch(location.pathname+'?token='+encodeURIComponent(token),{method:'POST',headers:{'Content-Type':'application/json',Accept:'application/json'},body:JSON.stringify({command:document.querySelector('#command').value})}); status.textContent=JSON.stringify(await r.json(),null,2)}catch(e){status.textContent='发送失败：'+e}finally{button.disabled=false}} load()</script>""".encode()
                self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body); return
            self._send(service.apply_command(parse_command("状态")))

        def do_POST(self):
            path = urlparse(self.path).path
            if not self._authorized():
                return self._api_response("", error={"code": "unauthorized", "message": "令牌无效"}, status=401)
            payload = self._read_json()
            if path != "/api/v1/commands":
                return self._api_response("", error={"code": "not_found", "message": "接口不存在"}, status=404)
            if not isinstance(payload, dict):
                return self._api_response("", error={"code": "invalid_json", "message": "请求体必须是 JSON 对象"}, status=400)
            request_id = self._request_id(payload)
            if not request_id:
                return self._api_response("", error={"code": "missing_request_id", "message": "缺少 request_id"}, status=422)
            command_text = str(payload.get("command", "")).strip()
            if not command_text:
                return self._api_response(request_id, error={"code": "missing_command", "message": "缺少 command"}, status=422)
            command = parse_command(command_text)
            if command.kind == "help":
                return self._api_response(request_id, error={"code": "invalid_command", "message": "无法识别命令"}, status=422)
            previous = service.repo.get_command(request_id)
            if previous:
                replay = json.loads(previous["response"])
                replay["replayed"] = True
                return self._send(replay)
            day = payload.get("date")
            result = service.apply_command(command, day)
            data = result.get("data")
            if data is None:
                data = {key: value for key, value in result.items() if key not in {"ok", "message"}}
            data.setdefault("date", day)
            response = {"ok": bool(result.get("ok")), "request_id": request_id, "message": result.get("message", ""), "data": data}
            self._send(response, 200 if response["ok"] else 422)
            service.repo.record_command(request_id, command_text, response)

    return Handler


def serve(service, token, host="127.0.0.1", port=8765):
    ThreadingHTTPServer((host, port), make_handler(service, token)).serve_forever()
