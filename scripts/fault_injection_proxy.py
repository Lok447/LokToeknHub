"""Pre-production fault injection proxy for DeepSeek gateway drills."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json, os, time, urllib.request

KEY = os.environ["DEEPSEEK_UAT_KEY"]
UPSTREAM = "https://api.deepseek.com"

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def reply(self, status, body):
        raw = json.dumps(body, separators=(",", ":")).encode()
        self.send_response(status); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw)
    def do_GET(self): self.forward()
    def do_POST(self): self.forward()
    def forward(self):
        mode = self.headers.get("X-Fault-Mode", "").lower()
        if "/fault500/" in self.path: mode = "500"
        elif "/fault429/" in self.path: mode = "429"
        elif "/fault-timeout/" in self.path: mode = "timeout"
        if mode == "timeout": time.sleep(8); return
        if mode == "429": self.reply(429, {"error": {"message": "injected rate limit"}}); return
        if mode == "500": self.reply(500, {"error": {"message": "injected upstream failure"}}); return
        length = int(self.headers.get("Content-Length", "0")); data = self.rfile.read(length) if length else None
        req = urllib.request.Request(UPSTREAM + self.path, data=data, method=self.command, headers={"Authorization": "Bearer " + KEY, "Content-Type": self.headers.get("Content-Type", "application/json")})
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                body = resp.read(); self.send_response(resp.status); self.send_header("Content-Type", resp.headers.get("Content-Type", "application/json")); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
        except Exception as exc:
            self.reply(502, {"error": {"message": str(exc)[:200]}})
    def log_message(self, *_): return

if __name__ == "__main__": ThreadingHTTPServer(("127.0.0.1", 4020), Handler).serve_forever()
