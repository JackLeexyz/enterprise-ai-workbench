from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
import hmac
from http.cookies import SimpleCookie
import json
import mimetypes
from pathlib import Path
import re
import threading
import time
from urllib.parse import parse_qs, quote, unquote, urlsplit
import uuid

from store import Problem, fail
from auth import REMEMBER_SECONDS, SESSION_SECONDS
from document_import import ImportProblem, MAX_UPLOAD_BYTES, read_multipart


@dataclass
class Response:
    status: int
    body: bytes
    headers: dict = field(default_factory=dict)


class Application:
    def __init__(self, store, origin="http://127.0.0.1:4180", static=None):
        self.store = store
        parsed = urlsplit(origin)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.path not in {"", "/"}:
            raise ValueError("WORKBENCH_ORIGIN must be an exact HTTP(S) origin")
        self.origin = parsed.scheme + "://" + parsed.netloc
        self.host = parsed.netloc
        self.secure = parsed.scheme == "https"
        self.cookie_name = "__Host-workbench_session" if self.secure else "workbench_session"
        self.static = Path(static or Path(__file__).parent / "static").resolve()
        self.attempts = defaultdict(deque)
        self.lock = threading.Lock()

    def cookie(self, token, clear=False, remember=False):
        lifetime = REMEMBER_SECONDS if self.store.demo_mode and remember else SESSION_SECONDS
        return f"{self.cookie_name}={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={0 if clear else lifetime}" + ("; Secure" if self.secure else "")

    def login_options(self):
        choices = [("writer", "写作员工", "employee"), ("reviewer", "审核同事", "employee"), ("admin", "管理员", "admin")]
        users = {item["username"]: item for item in self.store.users()} if self.store.demo_mode else {}
        return {"demo": self.store.demo_mode, "password_min_length": self.store.password_min_length,
                "remember_seconds": REMEMBER_SECONDS if self.store.demo_mode else SESSION_SECONDS,
                "accounts": [{"username": name, "label": label} for name, label, role in choices
                             if name in users and users[name]["id"] == "u_" + name and users[name]["role"] == role]}

    @staticmethod
    def request_limit(method, target):
        return MAX_UPLOAD_BYTES if method == "POST" and unquote(urlsplit(target).path) == "/api/workbench/imports" else 512 * 1024

    def json_response(self, data, status=200, headers=None, request_id=""):
        return Response(status, json.dumps({"data": data, "request_id": request_id}, ensure_ascii=False).encode(), {"Content-Type": "application/json; charset=utf-8", **(headers or {})})

    def login_limit(self, address):
        with self.lock:
            current = time.monotonic()
            attempts = self.attempts[address]
            while attempts and attempts[0] < current - 300:
                attempts.popleft()
            if len(attempts) >= 12:
                fail(429, "RATE_LIMITED", "登录尝试过多，请 5 分钟后再试。")
            attempts.append(current)
            if len(self.attempts) > 2048:
                for key in list(self.attempts):
                    if not self.attempts[key] or self.attempts[key][-1] < current - 300:
                        self.attempts.pop(key, None)

    def handle(self, method, target, headers, payload=b"", address="127.0.0.1"):
        request_id = "req_" + uuid.uuid4().hex
        headers = {key.lower(): value for key, value in headers.items()}
        try:
            if headers.get("host") != self.host:
                fail(400, "INVALID_HOST", "请求域名不匹配。")
            parsed = urlsplit(target)
            path, query = unquote(parsed.path), parse_qs(parsed.query)
            if method == "GET" and not path.startswith("/api/"):
                response = self.asset(path)
            elif method == "GET" and path == "/api/workbench/health":
                response = self.json_response({"status": "ok", "mode": "offline_keyword", "version": "0.11.1"}, request_id=request_id)
            elif method == "GET" and path == "/api/workbench/login-options":
                response = self.json_response(self.login_options(), request_id=request_id)
            else:
                if not path.startswith("/api/workbench/"):
                    fail(404, "NOT_FOUND", "页面或接口不存在。")
                upload = method == "POST" and path == "/api/workbench/imports"
                if len(payload) > self.request_limit(method, target):
                    fail(413, "REQUEST_TOO_LARGE", "请求内容过大。")
                body = {}
                if method != "GET":
                    if headers.get("origin") != self.origin:
                        fail(403, "ORIGIN_REJECTED", "写请求来源不匹配。")
                    if not upload and headers.get("content-type", "").split(";")[0].strip() != "application/json":
                        fail(415, "JSON_REQUIRED", "此接口只接受 JSON。")
                    if not upload:
                        try:
                            body = json.loads(payload or b"{}")
                        except (ValueError, UnicodeDecodeError):
                            fail(400, "INVALID_JSON", "请求内容不是有效 JSON。")
                        if not isinstance(body, dict):
                            fail(400, "INVALID_JSON", "请求内容必须是对象。")
                if path == "/api/workbench/session" and method == "POST":
                    self.login_limit(address)
                    remember = body.get("remember", False)
                    token, user = self.store.login(body.get("username"), body.get("password"), remember)
                    response = self.json_response(user, headers={"Set-Cookie": self.cookie(token, remember=remember)}, request_id=request_id)
                else:
                    cookie = SimpleCookie()
                    try:
                        cookie.load(headers.get("cookie", ""))
                    except Exception:
                        fail(401, "SESSION_EXPIRED", "请重新登录。")
                    token = cookie[self.cookie_name].value if self.cookie_name in cookie else ""
                    user = self.store.session(token)
                    if method != "GET" and not hmac.compare_digest(headers.get("x-csrf-token", ""), user["csrf"]):
                        fail(403, "CSRF_REJECTED", "会话校验失败，请刷新后重试。")
                    if path == "/api/workbench/session/logout" and method == "POST":
                        self.store.logout(token)
                        response = self.json_response({"logged_out": True}, headers={"Set-Cookie": self.cookie("", True)}, request_id=request_id)
                    elif upload:
                        try:
                            filename, content = read_multipart(headers.get("content-type", ""), payload)
                        except ImportProblem as error:
                            fail(413 if error.code == "FILE_TOO_LARGE" else 422, error.code, error.message)
                        parent, parent_version = headers.get("x-source-parent"), headers.get("x-source-version")
                        if (parent is None) != (parent_version is None) or (parent_version is not None and not re.fullmatch(r"[1-9][0-9]{0,8}", parent_version)):
                            fail(422, "INVALID_INPUT", "新版本上传需要成对提供目标资料及有效版本号。")
                        data = self.store.enqueue_import(user, filename, content, headers.get("idempotency-key"), parent, int(parent_version) if parent_version is not None else None)
                        response = self.json_response(data, status=202, request_id=request_id)
                    else:
                        response = self.dispatch(method, path, query, body, user, headers.get("idempotency-key"), request_id)
        except Problem as error:
            response = Response(error.status, json.dumps({"error": {"code": error.code, "message": error.message}, "request_id": request_id}, ensure_ascii=False).encode(), {"Content-Type": "application/json; charset=utf-8"})
            if error.status == 429:
                response.headers["Retry-After"] = "300"
        except Exception:
            # Never echo private content, SQL, paths, credentials or tracebacks to clients.
            response = Response(500, json.dumps({"error": {"code": "INTERNAL_ERROR", "message": "操作未完成，请稍后重试并提供请求编号。"}, "request_id": request_id}).encode(), {"Content-Type": "application/json; charset=utf-8"})
        response.headers.update({"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY", "Referrer-Policy": "same-origin", "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'", "X-Request-Id": request_id})
        return response

    def asset(self, path):
        relative = "index.html" if path in {"", "/"} else path.removeprefix("/static/") if path.startswith("/static/") else None
        if relative is None:
            fail(404, "NOT_FOUND", "页面不存在。")
        target = (self.static / relative).resolve()
        if not target.is_relative_to(self.static) or not target.is_file() or target.suffix not in {".html", ".css", ".js", ".png", ".txt"}:
            fail(404, "NOT_FOUND", "资源不存在。")
        return Response(200, target.read_bytes(), {"Content-Type": mimetypes.guess_type(str(target))[0] or "application/octet-stream"})

    def dispatch(self, method, path, query, body, user, key, request_id):
        s, prefix = self.store, "/api/workbench"
        endpoint = path[len(prefix):]
        if method == "GET":
            if endpoint == "/session":
                data = user
            elif endpoint == "/users":
                data = s.users()
            elif endpoint == "/categories":
                data = s.categories()
            elif endpoint == "/admin/users":
                data = s.admin_users(user)
            elif endpoint == "/admin/categories":
                data = s.admin_categories(user)
            elif endpoint == "/templates":
                data = s.templates()
            elif endpoint == "/admin/templates":
                data = s.admin_templates(user)
            elif match := re.fullmatch(r"/templates/versions/([A-Za-z0-9_-]+)", endpoint):
                data = s.template_version(match[1])
            elif endpoint == "/sources":
                data = s.list_sources(user, query.get("q", [""])[0][:120])
            elif endpoint == "/artifacts":
                data = s.list_artifacts(user)
            elif endpoint == "/reviews":
                data = s.reviews(user)
            elif endpoint == "/messages":
                data = s.messages(user)
            elif endpoint == "/conversations":
                data = s.list_conversations(user)
            elif match := re.fullmatch(r"/conversations/([A-Za-z0-9_-]+)/messages", endpoint):
                data = s.conversation_messages(user, match[1], query.get("cursor", [None])[0])
            elif match := re.fullmatch(r"/conversations/([A-Za-z0-9_-]+)", endpoint):
                data = s.get_conversation(user, match[1])
            elif endpoint == "/tasks":
                data = s.tasks(user)
            elif endpoint == "/imports":
                data = s.list_imports(user)
            elif endpoint == "/imports/usage":
                data = s.import_usage(user)
            elif endpoint == "/admin":
                data = s.admin_summary(user)
            elif endpoint == "/admin/publications":
                data = s.admin_publications(user)
            elif endpoint == "/admin/feedback":
                data = s.admin_feedback(user)
            elif match := re.fullmatch(r"/messages/([A-Za-z0-9_-]+)/evidence/(ev_[a-f0-9]{64})", endpoint):
                data = s.get_evidence(user, match[1], match[2])
            elif match := re.fullmatch(r"/sources/([A-Za-z0-9_-]+)", endpoint):
                data = s.get_source(user, match[1])
            elif match := re.fullmatch(r"/imports/([A-Za-z0-9_-]+)", endpoint):
                data = s.get_import(user, match[1])
            elif match := re.fullmatch(r"/sources/([A-Za-z0-9_-]+)/file", endpoint):
                content, filename = s.source_file(user, match[1])
                return Response(200, content, {"Content-Type": "application/octet-stream", "Content-Disposition": "attachment; filename=source-file; filename*=UTF-8''" + quote(filename, safe="")})
            elif match := re.fullmatch(r"/artifacts/([A-Za-z0-9_-]+)(?:/revisions/([0-9]+))?", endpoint):
                data = s.get_artifact(user, match[1], int(match[2]) if match[2] else None)
            elif match := re.fullmatch(r"/artifacts/([A-Za-z0-9_-]+)/checks", endpoint):
                data = s.get_artifact(user, match[1])["checks"]
            elif match := re.fullmatch(r"/artifacts/([A-Za-z0-9_-]+)/draft", endpoint):
                data = s.get_working_copy(user, match[1])
            elif match := re.fullmatch(r"/artifacts/([A-Za-z0-9_-]+)/suggestions/([A-Za-z0-9_-]+)", endpoint):
                data = s.get_suggestion(user, match[1], match[2])
            elif match := re.fullmatch(r"/exports/([A-Za-z0-9_-]+)/download", endpoint):
                content, title = s.download_export(user, match[1])
                return Response(200, content, {"Content-Type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "Content-Disposition": "attachment; filename=workbench-document.docx; filename*=UTF-8''" + quote(title + ".docx")})
            else:
                fail(404, "NOT_FOUND", "接口不存在。")
        elif method == "POST":
            if endpoint == "/admin/users":
                data = s.create_user(user, body, key)
            elif endpoint == "/admin/categories":
                data = s.create_category(user, body, key)
            elif match := re.fullmatch(r"/admin/users/([A-Za-z0-9_-]+)/password", endpoint):
                data = s.reset_user_password(user, match[1], body, key)
            elif endpoint == "/sources":
                data = s.create_source(user, body, key)
            elif match := re.fullmatch(r"/sources/([A-Za-z0-9_-]+)/versions", endpoint):
                data = s.add_source_version(user, match[1], body, key)
            elif endpoint == "/artifacts":
                data = s.create_artifact(user, body, key)
            elif match := re.fullmatch(r"/artifacts/([A-Za-z0-9_-]+)/draft", endpoint):
                data = s.save_working_copy(user, match[1], body, key)
            elif match := re.fullmatch(r"/artifacts/([A-Za-z0-9_-]+)/draft/discard", endpoint):
                data = s.discard_working_copy(user, match[1], body, key)
            elif match := re.fullmatch(r"/admin/templates/([A-Za-z0-9_-]+)/versions", endpoint):
                data = s.update_template(user, match[1], body, key)
            elif match := re.fullmatch(r"/artifacts/([A-Za-z0-9_-]+)/suggestions", endpoint):
                data = s.create_suggestion(user, match[1], body, key)
            elif match := re.fullmatch(r"/artifacts/([A-Za-z0-9_-]+)/suggestions/([A-Za-z0-9_-]+)/(apply|cancel|undo)", endpoint):
                handler = {"apply": s.apply_suggestion, "cancel": s.cancel_suggestion, "undo": s.undo_suggestion}[match[3]]
                data = handler(user, match[1], match[2], body, key)
            elif endpoint == "/messages":
                data = s.ask(user, body, key)
            elif endpoint == "/conversations":
                data = s.create_conversation(user, body, key)
            elif match := re.fullmatch(r"/conversations/([A-Za-z0-9_-]+)/messages", endpoint):
                data = s.ask_conversation(user, match[1], body, key)
            elif match := re.fullmatch(r"/messages/([A-Za-z0-9_-]+)/feedback", endpoint):
                data = s.submit_feedback(user, match[1], body, key)
            elif match := re.fullmatch(r"/admin/feedback/([A-Za-z0-9_-]+)/status", endpoint):
                data = s.update_feedback(user, match[1], body, key)
            elif endpoint == "/tasks/preview":
                data = s.task_preview(user, body, key)
            elif match := re.fullmatch(r"/imports/([A-Za-z0-9_-]+)/(confirm|retry|cancel|delete)", endpoint):
                handler = {"confirm": s.commit_import, "retry": s.retry_import, "cancel": s.cancel_import, "delete": s.delete_import}[match[2]]
                data = handler(user, match[1], body, key)
            elif match := re.fullmatch(r"/sources/([A-Za-z0-9_-]+)/publication-requests", endpoint):
                data = s.request_publication(user, match[1], body, key)
            elif match := re.fullmatch(r"/sources/([A-Za-z0-9_-]+)/withdraw", endpoint):
                data = s.withdraw_source(user, match[1], body, key)
            elif match := re.fullmatch(r"/admin/publications/([A-Za-z0-9_-]+)/decision", endpoint):
                data = s.decide_publication(user, match[1], body, key)
            elif match := re.fullmatch(r"/artifacts/([A-Za-z0-9_-]+)/(revisions|reviews|comments|exports)", endpoint):
                handlers = {"revisions": s.save_artifact, "reviews": s.submit_review, "comments": s.comment, "exports": s.create_export}
                data = handlers[match[2]](user, match[1], body, key)
            elif match := re.fullmatch(r"/reviews/([A-Za-z0-9_-]+)/decision", endpoint):
                data = s.decide_review(user, match[1], body, key)
            elif match := re.fullmatch(r"/task-previews/([A-Za-z0-9_-]+)/confirm", endpoint):
                data = s.confirm_task(user, match[1], body, key)
            elif match := re.fullmatch(r"/tasks/([A-Za-z0-9_-]+)/status", endpoint):
                data = s.update_task(user, match[1], body, key)
            else:
                fail(404, "NOT_FOUND", "接口不存在。")
        elif method == "PATCH":
            if match := re.fullmatch(r"/conversations/([A-Za-z0-9_-]+)", endpoint):
                data = s.update_conversation(user, match[1], body, key)
            elif match := re.fullmatch(r"/artifacts/([A-Za-z0-9_-]+)/sharing", endpoint):
                data = s.share_artifact(user, match[1], body, key)
            elif match := re.fullmatch(r"/sources/([A-Za-z0-9_-]+)/sharing", endpoint):
                data = s.share_source(user, match[1], body, key)
            elif match := re.fullmatch(r"/admin/users/([A-Za-z0-9_-]+)", endpoint):
                data = s.update_user(user, match[1], body, key)
            elif match := re.fullmatch(r"/admin/categories/([A-Za-z0-9_-]+)", endpoint):
                data = s.update_category(user, match[1], body, key)
            else:
                fail(404, "NOT_FOUND", "接口不存在。")
        else:
            fail(405, "METHOD_NOT_ALLOWED", "不支持此请求方式。")
        headers = {"Set-Cookie": self.cookie("", True)} if isinstance(data, dict) and data.get("reauthenticate") else None
        return self.json_response(data, headers=headers, request_id=request_id)
