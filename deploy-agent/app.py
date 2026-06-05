import json
import os
import pathlib
import shlex
import subprocess
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


LISTEN_HOST = os.environ.get("DEPLOY_AGENT_LISTEN_HOST", "0.0.0.0")
LISTEN_PORT = int(os.environ.get("DEPLOY_AGENT_LISTEN_PORT", "8090"))
WORKSPACE_ROOT = pathlib.Path(os.environ.get("DEPLOY_AGENT_WORKSPACE_ROOT", "/workspace")).resolve()
AUTH_TOKEN = os.environ.get("DEPLOY_AGENT_TOKEN", "")
DEFAULT_TIMEOUT = int(os.environ.get("DEPLOY_AGENT_DEFAULT_TIMEOUT", "30"))
MAX_TIMEOUT = int(os.environ.get("DEPLOY_AGENT_MAX_TIMEOUT", "180"))


def is_allowed_path(path_str: str) -> pathlib.Path:
    path = pathlib.Path(path_str)
    if not path.is_absolute():
        path = WORKSPACE_ROOT / path
    path = path.resolve()
    if WORKSPACE_ROOT == path or WORKSPACE_ROOT in path.parents:
        return path
    raise ValueError("path outside allowed workspace")


def ensure_auth(handler):
    if not AUTH_TOKEN:
        return True
    auth = handler.headers.get("Authorization", "")
    return auth == f"Bearer {AUTH_TOKEN}"


class Handler(BaseHTTPRequestHandler):
    server_version = "deploy-agent/1.0"

    def log_message(self, fmt, *args):
        return

    def _json(self, payload, code=200):
        data = json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _read_json(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length > 0 else b"{}"
            return json.loads(raw.decode("utf-8"))
        except Exception as e:
            raise ValueError(f"invalid json: {e}")

    def _forbidden(self):
        return self._json({"ok": False, "error": "forbidden"}, code=403)

    def do_GET(self):
        if not ensure_auth(self):
            return self._forbidden()

        if self.path == "/health":
            return self._json(
                {
                    "ok": True,
                    "service": "deploy-agent",
                    "mode": "write-exec",
                    "workspace_root": str(WORKSPACE_ROOT),
                    "token_required": bool(AUTH_TOKEN),
                }
            )

        return self._json({"ok": False, "error": "not_found"}, code=404)

    def do_POST(self):
        if not ensure_auth(self):
            return self._forbidden()

        if self.path == "/exec":
            return self.handle_exec()
        if self.path == "/write-file":
            return self.handle_write_file()
        if self.path == "/read-file":
            return self.handle_read_file()
        if self.path == "/mkdir":
            return self.handle_mkdir()

        return self._json({"ok": False, "error": "not_found"}, code=404)

    def handle_exec(self):
        try:
            body = self._read_json()
            command = body.get("command", "")
            if not command or not isinstance(command, str):
                return self._json({"ok": False, "error": "command required"}, code=400)

            workdir = is_allowed_path(body.get("workdir", "."))
            timeout = int(body.get("timeout", DEFAULT_TIMEOUT))
            timeout = max(1, min(timeout, MAX_TIMEOUT))

            proc = subprocess.run(
                command,
                shell=True,
                cwd=str(workdir),
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            return self._json(
                {
                    "ok": True,
                    "command": command,
                    "workdir": str(workdir),
                    "returncode": proc.returncode,
                    "stdout": proc.stdout,
                    "stderr": proc.stderr,
                }
            )
        except subprocess.TimeoutExpired as e:
            return self._json(
                {
                    "ok": False,
                    "error": "timeout",
                    "command": e.cmd,
                    "timeout": e.timeout,
                    "stdout": e.stdout or "",
                    "stderr": e.stderr or "",
                },
                code=408,
            )
        except Exception as e:
            return self._json({"ok": False, "error": str(e)}, code=400)

    def handle_write_file(self):
        try:
            body = self._read_json()
            path = is_allowed_path(body["path"])
            content = body.get("content", "")
            if not isinstance(content, str):
                return self._json({"ok": False, "error": "content must be string"}, code=400)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            return self._json({"ok": True, "path": str(path), "bytes": len(content.encode("utf-8"))})
        except Exception as e:
            return self._json({"ok": False, "error": str(e)}, code=400)

    def handle_read_file(self):
        try:
            body = self._read_json()
            path = is_allowed_path(body["path"])
            content = path.read_text(encoding="utf-8")
            return self._json({"ok": True, "path": str(path), "content": content})
        except Exception as e:
            return self._json({"ok": False, "error": str(e)}, code=400)

    def handle_mkdir(self):
        try:
            body = self._read_json()
            path = is_allowed_path(body["path"])
            path.mkdir(parents=True, exist_ok=True)
            return self._json({"ok": True, "path": str(path)})
        except Exception as e:
            return self._json({"ok": False, "error": str(e)}, code=400)


if __name__ == "__main__":
    WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((LISTEN_HOST, LISTEN_PORT), Handler)
    print(f"deploy-agent listening on {LISTEN_HOST}:{LISTEN_PORT}", flush=True)
    server.serve_forever()
