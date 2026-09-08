from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import time


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, status, payload, content_type="application/json"):
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.endswith("/models"):
            if self.path.startswith("/fail/"):
                self._send(503, {"error": {"message": "simulated primary outage"}})
            else:
                self._send(200, {"data": [{"id": "preprod-model", "object": "model"}]})
            return
        self._send(404, {"error": {"message": "not found"}})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length) or b"{}")
        if self.path.startswith("/fail/"):
            self._send(503, {"error": {"message": "simulated primary outage"}})
            return
        if self.path.endswith("/chat/completions"):
            usage = {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}
            if payload.get("stream"):
                chunks = [
                    {"id": "preprod-stream", "object": "chat.completion.chunk", "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]},
                    {"id": "preprod-stream", "object": "chat.completion.chunk", "choices": [{"index": 0, "delta": {"content": "PREPROD OK"}, "finish_reason": None}]},
                    {"id": "preprod-stream", "object": "chat.completion.chunk", "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}], "usage": usage},
                ]
                body = b"".join((b"data: " + json.dumps(item, separators=(",", ":")).encode() + b"\n\n" for item in chunks)) + b"data: [DONE]\n\n"
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self._send(200, {"id": "preprod-chat", "object": "chat.completion", "created": int(time.time()), "model": payload.get("model"), "choices": [{"index": 0, "message": {"role": "assistant", "content": "PREPROD OK"}, "finish_reason": "stop"}], "usage": usage})
            return
        self._send(404, {"error": {"message": "not found"}})

    def log_message(self, *_args):
        return


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 4010), Handler).serve_forever()
