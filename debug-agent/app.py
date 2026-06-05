import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


ROOT = os.environ.get("DEBUG_AGENT_ROOT", "/host")
LISTEN_HOST = os.environ.get("DEBUG_AGENT_LISTEN_HOST", "0.0.0.0")
LISTEN_PORT = int(os.environ.get("DEBUG_AGENT_LISTEN_PORT", "8080"))
DOCKER_SOCKET_URL = "http://localhost"
DOCKER_SOCKET_PATH = "/var/run/docker.sock"

SAFE_ENV_KEYS = {
    "HOME",
    "HOSTNAME",
    "LANG",
    "PATH",
    "PWD",
    "SHELL",
    "SHLVL",
    "TERM",
    "USER",
}


def read_text(path):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except Exception as e:
        return f"<error reading {path}: {e}>"


def run_cmd(args):
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=10)
        return {
            "cmd": args,
            "returncode": proc.returncode,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
        }
    except Exception as e:
        return {"cmd": args, "error": str(e)}


def docker_get(path):
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(10)
        sock.connect(DOCKER_SOCKET_PATH)

        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: localhost\r\n"
            f"User-Agent: debug-agent/1.0\r\n"
            f"Accept: application/json\r\n"
            f"Connection: close\r\n"
            f"\r\n"
        )
        sock.sendall(request.encode("utf-8"))

        chunks = []
        while True:
            data = sock.recv(65536)
            if not data:
                break
            chunks.append(data)
        sock.close()

        raw = b"".join(chunks)
        head, _, body = raw.partition(b"\r\n\r\n")
        header_lines = head.decode("utf-8", "replace").splitlines()
        status_line = header_lines[0] if header_lines else "HTTP/1.1 500 Invalid response"
        parts = status_line.split(" ", 2)
        status_code = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 500
        headers = {}
        for line in header_lines[1:]:
            if ":" in line:
                k, v = line.split(":", 1)
                headers[k.strip().lower()] = v.strip()

        if status_code >= 400:
            return {
                "error": f"docker_api_http_{status_code}",
                "path": path,
                "status_code": status_code,
                "body": body.decode("utf-8", "replace")[:2000],
            }

        if headers.get("transfer-encoding", "").lower() == "chunked":
            body = decode_chunked(body)

        text = body.decode("utf-8", "replace").strip()
        return json.loads(text) if text else {}
    except Exception as e:
        return {"error": str(e), "path": path}


def decode_chunked(body):
    i = 0
    out = bytearray()
    total = len(body)
    while i < total:
        line_end = body.find(b"\r\n", i)
        if line_end == -1:
            break
        chunk_size_line = body[i:line_end].split(b";", 1)[0].strip()
        if not chunk_size_line:
            i = line_end + 2
            continue
        chunk_size = int(chunk_size_line, 16)
        i = line_end + 2
        if chunk_size == 0:
            break
        out.extend(body[i:i + chunk_size])
        i += chunk_size + 2
    return bytes(out)


class Handler(BaseHTTPRequestHandler):
    def _json(self, payload, code=200):
        data = json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):
        sys.stderr.write("%s - - [%s] %s\n" % (self.address_string(), self.log_date_time_string(), fmt % args))

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path

        if path == "/health":
            return self._json(
                {
                    "ok": True,
                    "service": "debug-agent",
                    "mode": "read-only",
                    "root_mount": ROOT,
                    "docker_socket_present": os.path.exists("/var/run/docker.sock"),
                }
            )

        if path == "/host-info":
            payload = {
                "hostname": socket.gethostname(),
                "platform": platform.platform(),
                "python": sys.version,
                "root_mount": ROOT,
                "os_release": read_text(os.path.join(ROOT, "etc/os-release")),
                "uname": run_cmd(["uname", "-a"]),
                "uptime": read_text(os.path.join(ROOT, "proc/uptime")),
                "cpuinfo_excerpt": read_text(os.path.join(ROOT, "proc/cpuinfo"))[:4000],
                "meminfo_excerpt": read_text(os.path.join(ROOT, "proc/meminfo"))[:4000],
            }
            return self._json(payload)

        if path == "/docker-info":
            payload = {
                "version": docker_get("/version"),
                "info": docker_get("/info"),
                "containers": docker_get("/containers/json?all=true"),
            }
            return self._json(payload)

        if path == "/disk":
            usage = shutil.disk_usage(ROOT)
            payload = {
                "root_mount": ROOT,
                "disk_usage": {
                    "total_bytes": usage.total,
                    "used_bytes": usage.used,
                    "free_bytes": usage.free,
                },
                "df_h": run_cmd(["df", "-h"]),
                "mounts_excerpt": read_text(os.path.join(ROOT, "proc/mounts"))[:4000],
            }
            return self._json(payload)

        if path == "/env-safe":
            payload = {k: os.environ.get(k) for k in sorted(SAFE_ENV_KEYS) if os.environ.get(k) is not None}
            return self._json(payload)

        if path == "/headers":
            payload = {
                "method": self.command,
                "path": self.path,
                "client_address": self.client_address[0],
                "headers": {k: v for k, v in self.headers.items()},
            }
            return self._json(payload)

        return self._json(
            {
                "ok": False,
                "error": "not_found",
                "available_routes": [
                    "/health",
                    "/host-info",
                    "/docker-info",
                    "/disk",
                    "/env-safe",
                    "/headers",
                ],
            },
            code=404,
        )


if __name__ == "__main__":
    server = ThreadingHTTPServer((LISTEN_HOST, LISTEN_PORT), Handler)
    print(f"debug-agent listening on {LISTEN_HOST}:{LISTEN_PORT}", flush=True)
    server.serve_forever()
