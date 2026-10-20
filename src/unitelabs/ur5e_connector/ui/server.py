import argparse
import asyncio
import concurrent.futures
import contextlib
import sys
import http.server
import json
import pathlib
import os
import secrets
import threading
import urllib.parse

from .service import PanelError, PanelService


class PanelServer(http.server.ThreadingHTTPServer):
    """Serve an isolated loopback UI with authenticated same-origin mutations."""

    daemon_threads = True

    def __init__(self, service: PanelService, loop: asyncio.AbstractEventLoop, port: int) -> None:
        self.service, self.loop = service, loop
        self.token = secrets.token_urlsafe(32)
        static = pathlib.Path(__file__).parent / "static"
        self.assets = {
            "/": ("text/html; charset=utf-8", (static / "index.html").read_bytes()),
            "/routines.json": ("application/json", (static / "routines.json").read_bytes()),
            "/app.js": ("text/javascript; charset=utf-8", (static / "app.js").read_bytes()),
            "/style.css": ("text/css; charset=utf-8", (static / "style.css").read_bytes()),
        }
        super().__init__(("127.0.0.1", port), PanelHandler)
        actual_port = self.server_address[1]
        self.hosts = {f"127.0.0.1:{actual_port}", f"localhost:{actual_port}"}


class PanelHandler(http.server.BaseHTTPRequestHandler):
    """Translate a small HTTP API to the panel's SiLA consumer."""

    server: PanelServer

    def log_message(self, message_format: str, *args: object) -> None:
        """Keep local request logs and session tokens out of stdout."""

    def _allowed(self, mutation: bool = False) -> bool:
        if self.headers.get("Host") not in self.server.hosts:
            self._json({"error": "Invalid host."}, 403)
            return False
        origin = self.headers.get("Origin")
        if origin is not None and origin not in {f"http://{host}" for host in self.server.hosts}:
            self._json({"error": "Cross-origin access is not allowed."}, 403)
            return False
        if self.headers.get("Sec-Fetch-Site") in {"cross-site", "same-site"}:
            self._json({"error": "Use the panel's own browser tab."}, 403)
            return False
        if mutation and not secrets.compare_digest(self.headers.get("X-Panel-Token", ""), self.server.token):
            self._json({"error": "Invalid panel session token."}, 403)
            return False
        return True

    def _send(self, data: bytes, mime: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'; "
            "connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; form-action 'self'",
        )
        self.end_headers()
        with contextlib.suppress(BrokenPipeError, ConnectionResetError):
            self.wfile.write(data)

    def _json(self, data: dict, status: int = 200) -> None:
        self._send(json.dumps(data, allow_nan=False).encode(), "application/json", status)

    def _dispatch(self, coroutine: object) -> object:
        future = asyncio.run_coroutine_threadsafe(coroutine, self.server.loop)
        try:
            return future.result(timeout=30)
        except concurrent.futures.TimeoutError:
            future.cancel()
            raise

    def do_GET(self) -> None:
        """Serve the UI or read its live status without sending motion commands."""
        if not self._allowed():
            return
        route = urllib.parse.urlsplit(self.path).path
        if route in self.server.assets:
            mime, content = self.server.assets[route]
            self._send(content, mime)
        elif route == "/api/session":
            self._json({"token": self.server.token})
        elif route == "/api/status":
            try:
                self._json(self._dispatch(self.server.service.status()))
            except (PanelError, OSError, TimeoutError) as error:
                self._json({"error": str(error)}, 503)
        else:
            self._json({"error": "Not found."}, 404)

    def do_POST(self) -> None:
        """Accept explicit previews, runs, queue cancellation and taught poses only."""
        if not self._allowed(mutation=True):
            return
        try:
            if self.headers.get("Transfer-Encoding") or self.headers.get_content_type() != "application/json":
                msg = "Use a bounded JSON request."
                raise PanelError(msg)
            length = int(self.headers.get("Content-Length", "0"))
            if not 1 <= length <= 65536:
                msg = "Request body must be 1-65536 bytes."
                raise PanelError(msg)
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                msg = "A JSON object is required."
                raise PanelError(msg)
            route = urllib.parse.urlsplit(self.path).path
            if route == "/api/motion/check":
                result = self._dispatch(self.server.service.check_motion())
            elif route == "/api/motion/preview":
                result = self._dispatch(self.server.service.preview_continuous(payload))
            elif route == "/api/motion/run":
                result = self._dispatch(self.server.service.start_continuous(payload))
            elif route == "/api/motion/inspect":
                result = self._dispatch(self.server.service.inspect_failed_run())
            elif route == "/api/preview":
                result = self._dispatch(self.server.service.preview(payload))
            elif route == "/api/runs":
                result = self._dispatch(self.server.service.start(payload.get("preview_id"), payload.get("run_id")))
            elif route == "/api/cancel":
                result = self._dispatch(self.server.service.cancel())
            elif route == "/api/positions":
                result = self._dispatch(self.server.service.teach(payload.get("name")))
            else:
                self._json({"error": "Not found."}, 404)
                return
            self._json(result)
        except (PanelError, ValueError, TypeError, KeyError) as error:
            self._json({"error": str(error)}, 400)
        except FileExistsError:
            self._json({"error": "This run has already been reserved; it will not be replayed."}, 409)
        except (OSError, TimeoutError) as error:
            self._json({"error": str(error)}, 503)
        except Exception as error:  # noqa: BLE001 -- remote SiLA errors must produce a response, never a retry.
            self._json({"error": str(error)}, 503)


def main() -> None:
    """Start the local panel without starting any robot movement."""
    parser = argparse.ArgumentParser(description="Local UR5e SiLA control panel")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--read-port", type=int, default=50053)
    parser.add_argument("--control-port", type=int, default=50054)
    parser.add_argument("--simulation", action="store_true")
    parser.add_argument(
        "--data-dir",
        type=pathlib.Path,
        default=(
            pathlib.Path(os.environ["UR5E_DATA_DIR"]) / "panel-data"
            if "UR5E_DATA_DIR" in os.environ
            else pathlib.Path("panel-data")
        ),
    )
    args = parser.parse_args()
    if args.simulation:
        args.read_port = args.control_port = 50052
    service = PanelService(args.data_dir, args.read_port, args.control_port)
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    try:
        asyncio.run_coroutine_threadsafe(service.open(), loop).result(timeout=30)
        server = PanelServer(service, loop, args.port)
        sys.stdout.write(f"UR5e Control Panel: http://127.0.0.1:{server.server_address[1]}\n")
        sys.stdout.flush()
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
    finally:
        asyncio.run_coroutine_threadsafe(service.close(), loop).result(timeout=75)
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=5)


if __name__ == "__main__":
    main()
