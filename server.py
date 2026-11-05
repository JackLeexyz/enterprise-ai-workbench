from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import os
from pathlib import Path

from application import Application
from store import Store
from ingestion_worker import IngestionWorker


class Handler(BaseHTTPRequestHandler):
    server_version = "EnterpriseWorkbench/0.6"
    protocol_version = "HTTP/1.0"

    def log_message(self, format_string, *args):
        pass

    def process(self):
        self.connection.settimeout(20)
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = -1
        limit = self.server.app.request_limit(self.command, self.path)
        if self.headers.get("Transfer-Encoding") or length < 0 or length > limit:
            self.send_response(413 if length > limit else 400)
            self.send_header("Content-Length", "0")
            self.send_header("Connection", "close")
            self.end_headers()
            return
        try:
            payload = self.rfile.read(length)
        except (OSError, TimeoutError):
            self.send_error(408)
            return
        if len(payload) != length:
            self.send_error(400)
            return
        address = self.client_address[0]
        if self.server.trust_proxy and ipaddress.ip_address(address).is_loopback:
            try:
                address = str(ipaddress.ip_address(self.headers.get("X-Real-IP", address)))
            except ValueError:
                pass
        response = self.server.app.handle(self.command, self.path, dict(self.headers), payload, address)
        self.send_response(response.status)
        for name, value in response.headers.items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(response.body)))
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            self.wfile.write(response.body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    do_GET = do_POST = do_PATCH = do_DELETE = process


class Server(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 32

    def __init__(self, address, app, trust_proxy=False):
        self.app = app
        self.trust_proxy = trust_proxy
        super().__init__(address, Handler)
        self.worker = IngestionWorker(app.store)
        self.worker.start()

    def server_close(self):
        worker = getattr(self, "worker", None)
        if worker:
            worker.stop()
        super().server_close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bind", choices=["127.0.0.1", "0.0.0.0"], default="127.0.0.1", help="Use 0.0.0.0 only inside an explicitly isolated container")
    parser.add_argument("--port", type=int, default=4180)
    parser.add_argument("--database", default=str(Path(__file__).parent / "data/workbench.db"))
    parser.add_argument("--origin", default=os.getenv("WORKBENCH_ORIGIN", "http://127.0.0.1:4180"))
    parser.add_argument("--trust-proxy", action="store_true")
    args = parser.parse_args()
    store = Store(args.database, demo_mode=os.getenv("WORKBENCH_DEMO_LOGIN", "0") == "1")
    if not store.users():
        raise SystemExit("Database not initialized. Run setup_app.py first; no default password exists.")
    app = Application(store, args.origin)
    with Server((args.bind, args.port), app, args.trust_proxy) as server:
        print(json.dumps({"status": "listening", "origin": app.origin, "bind": f"{args.bind}:{args.port}", "mode": "offline_keyword"}), flush=True)
        server.serve_forever()


if __name__ == "__main__":
    main()
