"""Minimal stand-in for diode-auth used only by the benchmark stack.

Serves the two endpoints the data path needs:
  POST */token       -> client-credentials token (unsigned JWT, every scope)
  POST */introspect  -> {"active": true, ...} for any bearer token

Both baseline and candidate run against this same stub, so OAuth2/Hydra
cost is removed from the measurement for both sides equally. It is NOT a
security component; never expose it outside the benchmark network.
"""

import base64
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SCOPES = "diode:ingest diode:read netbox:read netbox:write"
TTL = 3600


def _b64(obj):
    raw = json.dumps(obj, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def make_token(client_id="bench"):
    now = int(time.time())
    claims = {"sub": client_id, "client_id": client_id, "scope": SCOPES,
              "scp": SCOPES.split(), "iat": now, "exp": now + TTL, "iss": "bench-auth"}
    return f"{_b64({'alg': 'none', 'typ': 'JWT'})}.{_b64(claims)}.", claims


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        path = self.path.split("?", 1)[0].rstrip("/")
        if path.endswith("/token"):
            token, _ = make_token()
            self._send(200, {"access_token": token, "token_type": "bearer",
                             "expires_in": TTL, "scope": SCOPES})
        elif path.endswith("/introspect"):
            _, claims = make_token()
            self._send(200, {"active": True, **claims})
        else:
            self._send(404, {"error": "not_found"})

    def do_GET(self):
        self._send(200, {"ok": True})

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
