from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import secrets
import sqlite3
import time
import uuid

from auth import REMEMBER_SECONDS, SESSION_SECONDS, check_password, password_hash, token_hash
import writing
import rich_content

IMPORT_STORAGE_BYTES = 20 * 1024 * 1024
IMPORT_PENDING_LIMIT = 5
DEFAULT_CATEGORIES = {"cat_other": "其他", "cat_general": "通用资料", "cat_product": "产品资料", "cat_implementation": "实施指南", "cat_service": "服务知识", "cat_policy": "内部制度", "cat_internal": "内部文档"}


class Problem(Exception):
    def __init__(self, status, code, message):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def fail(status, code, message):
    raise Problem(status, code, message)


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ident(prefix):
    return prefix + "_" + uuid.uuid4().hex


def dump(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def source_locator(section, index):
    if type(section.get("page_number")) is int and type(section.get("fragment_number")) is int and section["page_number"] > 0 and section["fragment_number"] > 0:
        return f"第 {section['page_number']} 页 / 片段 {section['fragment_number']}"
    return f"第 {index + 1} 节 {section['title']}"


def text(value, name, maximum=120, required=True):
    if not isinstance(value, str) or len(value) > maximum or (required and not value.strip()):
        fail(422, "INVALID_INPUT", f"请检查{name}，最多 {maximum} 字。")
    return value.strip()


def version(value):
    if type(value) is not int or value < 1:
        fail(422, "INVALID_INPUT", "缺少有效的修订或版本号。")
    return value


class Store:
    def __init__(self, path, demo_mode=False):
        self.demo_mode = demo_mode is True
        self.password_min_length = 6 if self.demo_mode else 20
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.executescript((Path(__file__).parent / "schema.sql").read_text())
            if "revision" not in {row["name"] for row in db.execute("PRAGMA table_info(comments)")}:
                db.execute("ALTER TABLE comments ADD COLUMN revision INTEGER NOT NULL DEFAULT 1")
            db.execute("UPDATE artifacts SET template='solution' WHERE template='产品方案'")
            db.execute("UPDATE artifacts SET template='delivery' WHERE template='交付说明'")
            for item in json.loads((Path(__file__).parent / "templates.json").read_text())["templates"]:
                definition = writing.definition(item)
                tid = item["template_id"]
                db.execute("INSERT OR IGNORE INTO templates(id) VALUES(?)", (tid,))
                db.execute("INSERT OR IGNORE INTO template_versions(id,template_id,number,name,fields_json,sections_json,created_at) VALUES(?,?,?,?,?,?,?)", ("tv_" + tid + "_1", tid, 1, definition["name"], dump(definition["fields"]), dump(definition["sections"]), now()))
        with self.connection(write=True) as db:
            self.initialize_management(db)

    def initialize_management(self, db):
        db.execute("INSERT OR IGNORE INTO account_versions(user_id) SELECT id FROM users")
        for cid, name in DEFAULT_CATEGORIES.items():
            db.execute("INSERT OR IGNORE INTO categories(id,name,updated_at) VALUES(?,?,?)", (cid, name, now()))
        known = {name: cid for cid, name in DEFAULT_CATEGORIES.items()}
        # Keep arbitrary legacy private labels out of the shared category catalog.
        for row in db.execute("SELECT s.id,s.category FROM sources s LEFT JOIN source_categories c ON c.source_id=s.id WHERE c.source_id IS NULL").fetchall():
            db.execute("INSERT INTO source_categories VALUES(?,?)", (row["id"], known.get(row["category"], "cat_other")))
        for row in db.execute("SELECT s.* FROM sources s LEFT JOIN source_versions v ON v.source_id=s.id WHERE v.source_id IS NULL").fetchall():
            self.register_source(db, row)
        db.execute("INSERT OR IGNORE INTO publication_context SELECT p.id,f.version FROM publication_requests p JOIN source_versions v ON v.source_id=p.source_id JOIN source_families f ON f.id=v.family_id WHERE p.status='pending'")

    @staticmethod
    def register_source(db, row, family_id=None, number=1):
        if family_id is None:
            family_id = "family_" + row["id"]
            db.execute("INSERT INTO source_families(id,current_source_id,updated_at) VALUES(?,?,?)", (family_id, row["id"] if row["publication"] == "published" else None, now()))
        db.execute("INSERT INTO source_versions VALUES(?,?,?)", (row["id"], family_id, number))

    @staticmethod
    def source_lineage(db, row):
        found = db.execute("SELECT v.family_id,v.number,f.version,f.current_source_id FROM source_versions v JOIN source_families f ON f.id=v.family_id WHERE v.source_id=?", (row["id"],)).fetchone()
        return dict(found) if found else {"family_id": "family_" + row["id"], "number": 1, "version": 1, "current_source_id": row["id"] if row["publication"] == "published" else None}

    def current_source(self, db, row):
        return bool(row and row["publication"] == "published" and self.source_lineage(db, row)["current_source_id"] == row["id"])

    @staticmethod
    def touch_source_family(db, family_id, current_source_id=None, replace_current=False):
        db.execute("UPDATE source_families SET version=version+1,updated_at=? WHERE id=?", (now(), family_id))
        if replace_current:
            db.execute("UPDATE source_families SET current_source_id=? WHERE id=?", (current_source_id, family_id))
        # Any scope/head change requires a fresh, explicit review grant.
        db.execute("UPDATE publication_requests SET status='superseded' WHERE status='pending' AND source_id IN (SELECT source_id FROM source_versions WHERE family_id=?)", (family_id,))

    @contextmanager
    def connection(self, write=False):
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA busy_timeout=10000")
        try:
            if write:
                db.execute("BEGIN IMMEDIATE")
            yield db
            if write:
                db.commit()
        except Exception:
            if write:
                db.rollback()
            raise
        finally:
            db.close()

    def audit(self, db, user, action, object_id):
        db.execute("INSERT INTO audit(actor_id,action,object_id,created_at) VALUES(?,?,?,?)", (user["id"], action, object_id, now()))

    def once(self, db, user, route, key, payload, operation):
        current = db.execute("SELECT role FROM users WHERE id=? AND enabled=1", (user["id"],)).fetchone()
        if not current or current["role"] != user["role"]:
            fail(401, "SESSION_EXPIRED", "账号状态或权限已变化，请重新登录。")
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,100}", key):
            fail(422, "IDEMPOTENCY_REQUIRED", "请提供有效操作标识。")
        fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        previous = db.execute("SELECT * FROM idempotency WHERE user_id=? AND key=?", (user["id"], key)).fetchone()
        if previous:
            if previous["route"] != route or previous["payload_hash"] != fingerprint:
                fail(409, "IDEMPOTENCY_MISMATCH", "同一操作标识不能用于不同内容。")
            return json.loads(previous["result_json"])
        result = operation()
        db.execute("INSERT INTO idempotency VALUES(?,?,?,?,?,?)", (user["id"], key, route, fingerprint, dump(result), now()))
        return result

    def bootstrap(self, passwords, fixtures):
        with self.connection(write=True) as db:
            if db.execute("SELECT count(*) FROM users").fetchone()[0]:
                fail(409, "ALREADY_INITIALIZED", "此数据库已初始化，不能覆盖账号或数据。")
            for username, display, role in [("writer", "演示员工", "employee"), ("reviewer", "审核同事", "employee"), ("admin", "后台管理员", "admin")]:
                password = passwords[username]
                if not isinstance(password, str) or len(password) < 20:
                    fail(422, "WEAK_PASSWORD", "初始化口令至少 20 字符。")
                db.execute("INSERT INTO users(id,username,name,role,password_hash) VALUES(?,?,?,?,?)", ("u_" + username, username, display, role, password_hash(password)))
            for item in fixtures["sources"]:
                stamp = now()
                db.execute("INSERT INTO sources(id,version_id,title,category,label,owner_id,scope,publication,sections_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)", (item["id"], "ver_" + item["id"], item["title"], item["category"], item["version"], "u_writer" if item["id"] == "weekly" else "u_admin", "private" if item["id"] == "weekly" else "internal", "draft" if item["id"] == "weekly" else "published", dump(item["sections"]), stamp, stamp))
            for item in fixtures["documents"]:
                content = {"schema_version": 1, "blocks": []}
                for section in item["sections"]:
                    content["blocks"] += [{"id": ident("b"), "type": "heading", "text": section["title"]}, {"id": ident("b"), "type": "paragraph", "text": section["text"]}]
                refs = [{"source_id": sid, "version_id": "ver_" + sid} for sid in item["sources"]]
                stamp = now()
                template = "solution" if item["template"] == "产品方案" else "delivery"
                db.execute("INSERT INTO artifacts VALUES(?,?,?,?,?,?,?,?)", (item["id"], "u_writer", item["title"], template, 1, "draft", stamp, stamp))
                db.execute("INSERT INTO revisions VALUES(?,?,?,?,?,?,?)", (item["id"], 1, item["title"], dump(content), dump(refs), "u_writer", stamp))
            self.initialize_management(db)

    def users(self):
        with self.connection() as db:
            return [dict(row) for row in db.execute("SELECT id,username,name,role FROM users WHERE enabled=1 ORDER BY username")]

    @staticmethod
    def require_admin(db, user):
        if user["role"] != "admin" or not db.execute("SELECT 1 FROM users WHERE id=? AND role='admin' AND enabled=1", (user["id"],)).fetchone():
            fail(403, "ADMIN_REQUIRED", "需要当前有效的管理员权限。")

    @staticmethod
    def account_view(row):
        return {"id": row["id"], "username": row["username"], "name": row["name"], "role": row["role"], "active": bool(row["enabled"]), "version": row["version"], "updated_at": row["updated_at"]}

    @staticmethod
    def account_row(db, uid):
        row = db.execute("SELECT u.id,u.username,u.name,u.role,u.enabled,v.version,v.updated_at FROM users u JOIN account_versions v ON v.user_id=u.id WHERE u.id=?", (uid,)).fetchone()
        if not row:
            fail(404, "NOT_FOUND", "账号不存在。")
        return row

    def admin_users(self, user):
        with self.connection() as db:
            self.require_admin(db, user)
            return [self.account_view(row) for row in db.execute("SELECT u.id,u.username,u.name,u.role,u.enabled,v.version,v.updated_at FROM users u JOIN account_versions v ON v.user_id=u.id ORDER BY u.enabled DESC,u.username")]

    def account_password(self, value):
        minimum = self.password_min_length
        if not isinstance(value, str) or not minimum <= len(value) <= 200 or not value.strip() or any(ord(char) < 32 or ord(char) == 127 for char in value):
            fail(422, "INVALID_ACCOUNT", f"新密码需为 {minimum} 至 200 字符，不能包含控制字符。")
        return value

    def credential_once(self, db, user, route, key, payload, password, operation):
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,100}", key):
            fail(422, "IDEMPOTENCY_REQUIRED", "请提供有效操作标识。")
        previous = db.execute("SELECT password_hash FROM credential_intents WHERE user_id=? AND key=?", (user["id"], key)).fetchone()
        if previous and not check_password(password, previous[0]):
            fail(409, "IDEMPOTENCY_MISMATCH", "此操作标识已用于不同内容，请重新发起操作。")
        def write():
            encoded = password_hash(password)
            result = operation(encoded)
            db.execute("INSERT INTO credential_intents VALUES(?,?,?)", (user["id"], key, encoded))
            return result
        return self.once(db, user, route, key, payload, write)

    def create_user(self, user, payload, key):
        with self.connection(write=True) as db:
            self.require_admin(db, user)
            username, name, role = payload.get("username"), payload.get("name"), payload.get("role", "employee")
            if not isinstance(username, str) or not re.fullmatch(r"[a-z][a-z0-9_.-]{2,39}", username) or not isinstance(name, str) or not 1 <= len(name.strip()) <= 60 or role not in ("employee", "admin"):
                fail(422, "INVALID_ACCOUNT", "请检查账号、姓名及员工/管理员类型。账号为 3 至 40 位小写字母、数字或 ._-，以字母开头。")
            password = self.account_password(payload.get("password"))
            def operation(encoded):
                if db.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
                    fail(409, "ACCOUNT_EXISTS", "此账号已存在，请选择其他账号。")
                uid = ident("u")
                db.execute("INSERT INTO users(id,username,name,role,password_hash) VALUES(?,?,?,?,?)", (uid, username, name.strip(), role, encoded))
                db.execute("INSERT INTO account_versions(user_id,updated_at) VALUES(?,?)", (uid, now()))
                self.audit(db, user, "account_created", uid)
                return {"id": uid}
            result = self.credential_once(db, user, "account_create", key, {"username": username, "name": name.strip(), "role": role}, password, operation)
            return self.account_view(self.account_row(db, result["id"]))

    def update_user(self, user, uid, payload, key):
        with self.connection(write=True) as db:
            self.require_admin(db, user)
            base = version(payload.get("base_version"))
            if not set(payload).issubset({"base_version", "name", "role", "active"}) or not set(payload).intersection({"name", "role", "active"}) or ("active" in payload and type(payload["active"]) is not bool) or ("role" in payload and payload["role"] not in ("employee", "admin")) or ("name" in payload and (not isinstance(payload["name"], str) or not 1 <= len(payload["name"].strip()) <= 60)):
                fail(422, "INVALID_ACCOUNT", "请检查姓名、账号类型与启用状态。")
            def operation():
                row = self.account_row(db, uid)
                if row["version"] != base:
                    fail(409, "VERSION_CONFLICT", "账号已变化，请关闭窗口、刷新后重新编辑。")
                role, active = payload.get("role", row["role"]), payload.get("active", bool(row["enabled"]))
                name = payload.get("name", row["name"]).strip()
                revoked = role != row["role"] or active != bool(row["enabled"])
                if row["role"] == "admin" and row["enabled"] and (role != "admin" or not active) and db.execute("SELECT count(*) FROM users WHERE role='admin' AND enabled=1").fetchone()[0] <= 1:
                    fail(409, "LAST_ADMIN", "至少保留一个启用的管理员，不能停用或降级最后一个管理员。")
                if (name, role, active) == (row["name"], row["role"], bool(row["enabled"])):
                    fail(422, "NO_CHANGES", "账号内容没有变化。")
                db.execute("UPDATE users SET name=?,role=?,enabled=? WHERE id=?", (name, role, int(active), uid))
                db.execute("UPDATE account_versions SET version=version+1,updated_at=? WHERE user_id=?", (now(), uid))
                if revoked:
                    db.execute("DELETE FROM sessions WHERE user_id=?", (uid,))
                if not active or role != "admin":
                    db.execute("UPDATE publication_requests SET status='superseded' WHERE admin_id=? AND status='pending'", (uid,))
                self.audit(db, user, "account_updated", uid)
                return {"id": uid, "reauthenticate": revoked and uid == user["id"]}
            result = self.once(db, user, "account_update:" + uid, key, payload, operation)
            return {**self.account_view(self.account_row(db, uid)), "reauthenticate": result["reauthenticate"]}

    def reset_user_password(self, user, uid, payload, key):
        with self.connection(write=True) as db:
            self.require_admin(db, user)
            base, password = version(payload.get("base_version")), self.account_password(payload.get("password"))
            def operation(encoded):
                row = self.account_row(db, uid)
                if row["version"] != base:
                    fail(409, "VERSION_CONFLICT", "账号已变化，请刷新后再重置密码。")
                db.execute("UPDATE users SET password_hash=? WHERE id=?", (encoded, uid))
                db.execute("UPDATE account_versions SET version=version+1,updated_at=? WHERE user_id=?", (now(), uid))
                db.execute("DELETE FROM sessions WHERE user_id=?", (uid,))
                self.audit(db, user, "account_password_reset", uid)
                return {"id": uid, "reauthenticate": uid == user["id"]}
            result = self.credential_once(db, user, "account_password:" + uid, key, {"base_version": base}, password, operation)
            return {**self.account_view(self.account_row(db, uid)), "reauthenticate": result["reauthenticate"]}

    @staticmethod
    def category_view(row):
        return {"id": row["id"], "name": row["name"], "active": bool(row["active"]), "version": row["version"], "updated_at": row["updated_at"]}

    def categories(self):
        with self.connection() as db:
            return [self.category_view(row) for row in db.execute("SELECT * FROM categories WHERE active=1 ORDER BY id!='cat_other',name,id")]

    def admin_categories(self, user):
        with self.connection() as db:
            self.require_admin(db, user)
            return [self.category_view(row) for row in db.execute("SELECT * FROM categories ORDER BY id!='cat_other',active DESC,name,id")]

    def create_category(self, user, payload, key):
        with self.connection(write=True) as db:
            self.require_admin(db, user)
            name = text(payload.get("name"), "分类名称", 30)
            def operation():
                if db.execute("SELECT 1 FROM categories WHERE name=? COLLATE NOCASE", (name,)).fetchone():
                    fail(409, "CATEGORY_EXISTS", "分类名称已存在，停用分类可从后台重新启用。")
                cid = ident("cat")
                db.execute("INSERT INTO categories(id,name,updated_at) VALUES(?,?,?)", (cid, name, now()))
                self.audit(db, user, "category_created", cid)
                return {"id": cid}
            result = self.once(db, user, "category_create", key, payload, operation)
            return self.category_view(db.execute("SELECT * FROM categories WHERE id=?", (result["id"],)).fetchone())

    def update_category(self, user, cid, payload, key):
        with self.connection(write=True) as db:
            self.require_admin(db, user)
            base = version(payload.get("base_version"))
            if not set(payload).issubset({"base_version", "name", "active"}) or not set(payload).intersection({"name", "active"}) or ("active" in payload and type(payload["active"]) is not bool):
                fail(422, "INVALID_INPUT", "请检查分类名称与启用状态。")
            name = text(payload["name"], "分类名称", 30) if "name" in payload else None
            def operation():
                row = db.execute("SELECT * FROM categories WHERE id=?", (cid,)).fetchone()
                if not row:
                    fail(404, "NOT_FOUND", "分类不存在。")
                if row["version"] != base:
                    fail(409, "VERSION_CONFLICT", "分类已变化，请关闭窗口、刷新后重新编辑。")
                target_name, active = name if name is not None else row["name"], payload.get("active", bool(row["active"]))
                if cid == "cat_other" and (target_name != "其他" or not active):
                    fail(409, "DEFAULT_CATEGORY", "默认分类“其他”必须保留并启用。")
                if db.execute("SELECT 1 FROM categories WHERE name=? COLLATE NOCASE AND id!=?", (target_name, cid)).fetchone():
                    fail(409, "CATEGORY_EXISTS", "分类名称已存在。")
                if (target_name, active) == (row["name"], bool(row["active"])):
                    fail(422, "NO_CHANGES", "分类内容没有变化。")
                db.execute("UPDATE categories SET name=?,active=?,version=version+1,updated_at=? WHERE id=?", (target_name, int(active), now(), cid))
                self.audit(db, user, "category_updated", cid)
                return {"id": cid}
            self.once(db, user, "category_update:" + cid, key, payload, operation)
            return self.category_view(db.execute("SELECT * FROM categories WHERE id=?", (cid,)).fetchone())

    @staticmethod
    def selected_category(db, payload):
        if "category_id" in payload:
            cid = payload["category_id"]
            row = db.execute("SELECT * FROM categories WHERE id=? AND active=1", (cid if isinstance(cid, str) else "",)).fetchone()
        elif "category" in payload:
            name = text(payload["category"], "分类", 30)
            row = db.execute("SELECT * FROM categories WHERE name=? COLLATE NOCASE AND active=1", (name,)).fetchone()
        else:
            row = db.execute("SELECT * FROM categories WHERE id='cat_other' AND active=1").fetchone()
        if not row:
            fail(409, "CATEGORY_UNAVAILABLE", "所选分类已改名、停用或不存在，请刷新分类后重新选择；其他输入仍保留。")
        return row

    @staticmethod
    def source_category(db, row):
        category = db.execute("SELECT c.* FROM source_categories s JOIN categories c ON c.id=s.category_id WHERE s.source_id=?", (row["id"],)).fetchone()
        if not category:
            cid = next((cid for cid, name in DEFAULT_CATEGORIES.items() if name == row["category"]), "cat_other")
            category = db.execute("SELECT * FROM categories WHERE id=?", (cid,)).fetchone()
        return category

    def login(self, username, password, remember=False):
        if type(remember) is not bool:
            fail(422, "INVALID_INPUT", "请检查记住设备选项。")
        text(username, "用户名", 40)
        text(password, "密码", 200)
        with self.connection(write=True) as db:
            row = db.execute("SELECT * FROM users WHERE username=? AND enabled=1", (username,)).fetchone()
            dummy = db.execute("SELECT password_hash FROM users LIMIT 1").fetchone()
            if not dummy:
                fail(503, "NOT_INITIALIZED", "应用尚未初始化。")
            valid = check_password(password, row["password_hash"] if row else dummy[0])
            if not row or not valid:
                fail(401, "LOGIN_FAILED", "用户名或密码错误。")
            token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
            db.execute("DELETE FROM sessions WHERE expires<?", (time.time(),))
            lifetime = REMEMBER_SECONDS if self.demo_mode and remember else SESSION_SECONDS
            db.execute("INSERT INTO sessions VALUES(?,?,?,?)", (token_hash(token), row["id"], csrf, time.time() + lifetime))
            return token, {"id": row["id"], "username": row["username"], "name": row["name"], "role": row["role"], "csrf": csrf}

    def session(self, token):
        with self.connection() as db:
            row = db.execute("SELECT u.id,u.username,u.name,u.role,s.csrf FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=? AND s.expires>? AND u.enabled=1", (token_hash(token), time.time())).fetchone()
            if not row:
                fail(401, "SESSION_EXPIRED", "请重新登录；未保存内容请先保留。")
            return dict(row)

    def logout(self, token):
        with self.connection(write=True) as db:
            db.execute("DELETE FROM sessions WHERE token_hash=?", (token_hash(token),))

    def readable_source(self, db, user, row):
        if not row:
            return False
        owner = row["owner_id"] == user["id"]
        requested = bool(db.execute("SELECT 1 FROM publication_requests WHERE source_id=? AND admin_id=? AND status='pending'", (row["id"], user["id"])).fetchone())
        if row["publication"] == "withdrawn":
            return owner or requested
        return owner or requested or row["scope"] == "internal" or (row["scope"] == "selected" and user["id"] in json.loads(row["shares_json"]))

    def source(self, db, user, sid, published=False, current=False):
        row = db.execute("SELECT * FROM sources WHERE id=?", (sid,)).fetchone()
        if not self.readable_source(db, user, row) or (published and row["publication"] != "published") or (current and not self.current_source(db, row)):
            fail(404, "NOT_FOUND", "资料不存在、已撤回或无访问权限。")
        return row

    def source_view(self, db, row):
        result = dict(row)
        category = self.source_category(db, row)
        result.update(category_id=category["id"], category=category["name"], category_active=bool(category["active"]))
        result["legacy_category"] = row["category"] if category["id"] == "cat_other" and row["category"] != "其他" else ""
        result["sections"] = json.loads(result.pop("sections_json"))
        result["shared_user_ids"] = json.loads(result.pop("shares_json"))
        chain = self.source_lineage(db, row)
        result.update(family_id=chain["family_id"], family_version=chain["version"], version_number=chain["number"], is_current_published=self.current_source(db, row))
        return result

    def list_sources(self, user, query=""):
        with self.connection() as db:
            families = {}
            for row in db.execute("SELECT * FROM sources ORDER BY updated_at DESC,id"):
                if not self.readable_source(db, user, row):
                    continue
                item = self.source_view(db, row)
                previous = families.get(item["family_id"])
                if previous is None or item["version_number"] > previous["version_number"]:
                    families[item["family_id"]] = item
            rows = sorted(families.values(), key=lambda item: (item["updated_at"], item["id"]), reverse=True)
            for item in rows:
                current = db.execute("SELECT s.* FROM source_families f JOIN sources s ON s.id=f.current_source_id WHERE f.id=?", (item["family_id"],)).fetchone()
                item["published_source"] = self.source_view(db, current) if current and current["id"] != item["id"] and self.readable_source(db, user, current) and self.current_source(db, current) else None
            return [row for row in rows if not query or query.casefold() in (row["title"] + " " + row["category"]).casefold()][:100]

    def get_source(self, user, sid):
        with self.connection() as db:
            row = self.source(db, user, sid)
            result = self.source_view(db, row)
            attachment = db.execute("SELECT filename,file_size,file_sha256 FROM import_jobs WHERE source_id=? AND status='committed'", (sid,)).fetchone()
            result["attachment"] = dict(attachment) if attachment else None
            visible = [self.source_view(db, item) for item in db.execute("SELECT s.* FROM sources s JOIN source_versions v ON v.source_id=s.id WHERE v.family_id=? ORDER BY v.number DESC", (result["family_id"],)) if self.readable_source(db, user, item)]
            result["versions"] = [{field: item[field] for field in ("id", "version_id", "label", "title", "publication", "scope", "version_number", "is_current_published", "created_at")} for item in visible]
            latest = db.execute("SELECT max(number) FROM source_versions WHERE family_id=?", (result["family_id"],)).fetchone()[0] or 1
            result["can_add_version"] = row["owner_id"] == user["id"] and result["version_number"] == latest
            result["can_share"] = row["owner_id"] == user["id"] and row["publication"] != "withdrawn"
            return result

    def version_parent(self, db, user, sid, base):
        if not isinstance(sid, str) or not 1 <= len(sid) <= 80:
            fail(422, "INVALID_INPUT", "来源标识无效。")
        parent = self.source(db, user, sid)
        if parent["owner_id"] != user["id"]:
            fail(403, "OWNER_REQUIRED", "仅所有者可以上传新版本。")
        chain = self.source_lineage(db, parent)
        if chain["version"] != version(base):
            fail(409, "VERSION_CONFLICT", "资料版本或共享范围已变化，请保留内容并重新打开最新资料。")
        latest = db.execute("SELECT max(number) FROM source_versions WHERE family_id=?", (chain["family_id"],)).fetchone()[0] or 1
        if latest != chain["number"]:
            fail(409, "LATEST_VERSION_REQUIRED", "请从本人最新版本新建，避免产生分叉。")
        if latest >= 50:
            fail(409, "VERSION_LIMIT", "此演示资料已达到 50 个版本上限；不会自动覆盖历史。")
        return chain, latest

    def insert_source(self, db, user, payload, sections):
        title = text(payload.get("title"), "资料标题", 120)
        label = text(payload.get("label", "v1.0"), "版本", 30)
        category = self.selected_category(db, payload)
        family_id, number = None, 1
        parent_id = payload.get("parent_source_id")
        if parent_id is not None:
            chain, latest = self.version_parent(db, user, parent_id, payload.get("base_version"))
            family_id, number = chain["family_id"], latest + 1
            self.touch_source_family(db, family_id)
        sid, stamp = ident("src"), now()
        db.execute("INSERT INTO sources(id,version_id,title,category,label,owner_id,sections_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)", (sid, ident("ver"), title, category["name"], label, user["id"], dump(sections), stamp, stamp))
        db.execute("INSERT INTO source_categories VALUES(?,?)", (sid, category["id"]))
        self.register_source(db, db.execute("SELECT * FROM sources WHERE id=?", (sid,)).fetchone(), family_id, number)
        self.audit(db, user, "source_version_created" if parent_id else "source_created", sid)
        return sid

    @staticmethod
    def shared_scope(db, payload):
        scope, shares = payload.get("scope", "private"), payload.get("shared_user_ids", [])
        if scope not in {"private", "selected", "internal"} or not isinstance(shares, list) or len(shares) > 20 or (scope == "selected" and not shares):
            fail(422, "INVALID_SCOPE", "请选择有效的范围和至少一名指定同事。")
        if any(not isinstance(uid, str) or not db.execute("SELECT 1 FROM users WHERE id=? AND enabled=1", (uid,)).fetchone() for uid in shares):
            fail(422, "INVALID_SCOPE", "共享对象无效，请刷新同事列表。")
        return scope, sorted(set(shares)) if scope == "selected" else []

    def share_source(self, user, sid, payload, key):
        base = version(payload.get("base_version"))
        with self.connection(write=True) as db:
            def operation():
                row = self.source(db, user, sid)
                if row["owner_id"] != user["id"]:
                    fail(403, "OWNER_REQUIRED", "仅资料所有者可以调整共享。")
                chain = self.source_lineage(db, row)
                if chain["version"] != base or row["publication"] == "withdrawn":
                    fail(409, "VERSION_CONFLICT", "资料或范围已变化，或已撤回，请重新打开资料。")
                scope, shares = self.shared_scope(db, payload)
                if row["publication"] == "published":
                    db.execute("UPDATE sources SET scope=?,shares_json=?,version=version+1,updated_at=? WHERE publication='published' AND id IN (SELECT source_id FROM source_versions WHERE family_id=?)", (scope, dump(shares), now(), chain["family_id"]))
                else:
                    db.execute("UPDATE sources SET scope=?,shares_json=?,version=version+1,updated_at=? WHERE id=?", (scope, dump(shares), now(), sid))
                self.touch_source_family(db, chain["family_id"])
                self.audit(db, user, "source_sharing_changed", sid)
                return {"id": sid}
            result = self.once(db, user, "source_share:" + sid, key, payload, operation)
        return self.get_source(user, result["id"])

    def add_source_version(self, user, sid, payload, key):
        if "parent_source_id" in payload:
            fail(422, "INVALID_INPUT", "新版本入口已指定目标资料。")
        data = {**payload, "parent_source_id": sid}
        if "import_id" in data:
            if "text" in data or not isinstance(data["import_id"], str):
                fail(422, "INVALID_INPUT", "新版本只能选择已解析文件或文本其中一种。")
            result = self.commit_import(user, data["import_id"], data, key)
            return self.get_source(user, result["source_id"])
        body = text(payload.get("text"), "资料正文", 40000)
        with self.connection(write=True) as db:
            result = self.once(db, user, "source_version:" + sid, key, payload, lambda: {"id": self.insert_source(db, user, data, [{"title": "正文", "text": body}])})
        return self.get_source(user, result["id"])

    def import_view(self, row, content=False):
        result = dict(row)
        result.pop("file_bytes", None)
        sections = result.pop("sections_json")
        result["warnings"] = json.loads(result.pop("warnings_json"))
        if content:
            result["sections"] = json.loads(sections)
        return result

    def import_row(self, db, user, jid):
        row = db.execute("SELECT j.id,j.user_id,j.filename,j.file_sha256,j.file_size,j.status,j.sections_json,j.warnings_json,j.characters,j.attempts,j.error_code,j.error_message,j.source_id,j.created_at,j.updated_at,t.parent_source_id,t.base_family_version FROM import_jobs j LEFT JOIN import_targets t ON t.job_id=j.id WHERE j.id=? AND j.user_id=?", (jid, user["id"])).fetchone()
        if not row:
            fail(404, "NOT_FOUND", "导入记录不存在或无权限。")
        return row

    def list_imports(self, user):
        with self.connection() as db:
            return [self.import_view(row) for row in db.execute("SELECT j.id,j.user_id,j.filename,j.file_sha256,j.file_size,j.status,j.sections_json,j.warnings_json,j.characters,j.attempts,j.error_code,j.error_message,j.source_id,j.created_at,j.updated_at,t.parent_source_id,t.base_family_version FROM import_jobs j LEFT JOIN import_targets t ON t.job_id=j.id WHERE j.user_id=? ORDER BY j.created_at DESC,j.rowid DESC LIMIT 20", (user["id"],))]

    def get_import(self, user, jid):
        with self.connection() as db:
            return self.import_view(self.import_row(db, user, jid), True)

    def _import_usage(self, db, user):
        row = db.execute("SELECT coalesce(sum(file_size),0) AS used,count(*) AS retained,coalesce(sum(CASE WHEN status IN ('queued','running','canceling','preview') THEN 1 ELSE 0 END),0) AS pending FROM import_jobs WHERE user_id=?", (user["id"],)).fetchone()
        return {"used_bytes": row["used"], "limit_bytes": IMPORT_STORAGE_BYTES, "remaining_bytes": max(0, IMPORT_STORAGE_BYTES - row["used"]), "retained_count": row["retained"], "pending_count": row["pending"], "pending_limit": IMPORT_PENDING_LIMIT, "remaining_slots": max(0, IMPORT_PENDING_LIMIT - row["pending"])}

    def import_usage(self, user):
        with self.connection() as db:
            return self._import_usage(db, user)

    def enqueue_import(self, user, filename, content, key, parent_source_id=None, base_family_version=None):
        from document_import import ImportProblem, validate_file
        try:
            validate_file(filename, content)
        except ImportProblem as error:
            fail(413 if error.code == "FILE_TOO_LARGE" else 422, error.code, error.message)
        digest = hashlib.sha256(content).hexdigest()
        payload = {"filename": filename, "file_sha256": digest, "file_size": len(content)}
        if parent_source_id is None and base_family_version is not None:
            fail(422, "INVALID_INPUT", "请同时提供新版本目标和版本号。")
        if parent_source_id is not None:
            payload.update(parent_source_id=parent_source_id, base_family_version=base_family_version)
        with self.connection(write=True) as db:
            def operation():
                if parent_source_id is not None:
                    self.version_parent(db, user, parent_source_id, base_family_version)
                previous = db.execute("SELECT id FROM import_jobs WHERE user_id=? AND file_sha256=?", (user["id"], digest)).fetchone()
                if previous:
                    return {"id": previous["id"], "duplicate": True}
                usage = self._import_usage(db, user)
                if usage["used_bytes"] + len(content) > IMPORT_STORAGE_BYTES or usage["pending_count"] >= IMPORT_PENDING_LIMIT:
                    fail(409, "IMPORT_QUOTA", "演示附件额度或待确认队列已满，请先处理已有记录；不会自动删除资料。")
                jid, stamp = ident("import"), now()
                db.execute("INSERT INTO import_jobs(id,user_id,filename,file_sha256,file_size,file_bytes,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)", (jid, user["id"], filename, digest, len(content), content, stamp, stamp))
                if parent_source_id is not None:
                    db.execute("INSERT INTO import_targets VALUES(?,?,?)", (jid, parent_source_id, base_family_version))
                self.audit(db, user, "file_uploaded", jid)
                return {"id": jid, "duplicate": False}
            result = self.once(db, user, "import_create", key, payload, operation)
        try:
            current = self.get_import(user, result["id"])
        except Problem as error:
            if error.code != "NOT_FOUND":
                raise
            fail(410, "IMPORT_REMOVED", "此上传操作对应的文件已删除，请重新选择文件并发起新的上传。")
        return {**current, "duplicate": result["duplicate"]}

    def cancel_import(self, user, jid, payload, key):
        with self.connection(write=True) as db:
            def operation():
                row = self.import_row(db, user, jid)
                if row["source_id"] or row["status"] == "committed":
                    fail(409, "IMPORT_COMMITTED", "文件已经入库，请在资料中查看；不能取消或删除原件。")
                if row["status"] not in {"canceled", "canceling"}:
                    status = "canceling" if row["status"] == "running" else "canceled"
                    db.execute("UPDATE import_jobs SET status=?,sections_json='[]',warnings_json='[]',characters=0,error_code='',error_message='',updated_at=? WHERE id=?", (status, now(), jid))
                    self.audit(db, user, "import_cancel_requested", jid)
                return {"id": jid}
            self.once(db, user, "import_cancel:" + jid, key, payload, operation)
        return self.get_import(user, jid)

    def delete_import(self, user, jid, payload, key):
        if payload.get("confirm") is not True:
            fail(422, "DELETE_CONFIRM_REQUIRED", "请先明确确认删除这份未入库文件。")
        with self.connection(write=True) as db:
            def operation():
                row = self.import_row(db, user, jid)
                if row["source_id"] or row["status"] == "committed":
                    fail(409, "IMPORT_COMMITTED", "文件已经入库，请在资料中查看；不能取消或删除原件。")
                if row["status"] in {"running", "canceling"}:
                    fail(409, "IMPORT_BUSY", "解析仍在运行或取消待确认，请等待处理结束后再删除。")
                if payload.get("file_sha256") != row["file_sha256"]:
                    fail(409, "IMPORT_CHANGED", "文件校验信息不匹配，请重新打开记录后确认。")
                db.execute("DELETE FROM import_jobs WHERE id=? AND user_id=? AND source_id IS NULL", (jid, user["id"]))
                self.audit(db, user, "import_deleted", jid)
                return {"id": jid, "deleted": True, "released_bytes": row["file_size"]}
            return self.once(db, user, "import_delete:" + jid, key, payload, operation)

    def claim_import(self):
        with self.connection(write=True) as db:
            if db.execute("SELECT 1 FROM import_jobs WHERE status IN ('running','canceling')").fetchone():
                return None
            row = db.execute("SELECT * FROM import_jobs WHERE status='queued' ORDER BY created_at,rowid LIMIT 1").fetchone()
            if not row:
                return None
            db.execute("UPDATE import_jobs SET status='running',attempts=attempts+1,updated_at=? WHERE id=?", (now(), row["id"]))
            return dict(row)

    def finish_import(self, jid, result):
        with self.connection(write=True) as db:
            if self._finish_import_cancel(db, jid):
                return
            db.execute("UPDATE import_jobs SET status='preview',sections_json=?,warnings_json=?,characters=?,error_code='',error_message='',updated_at=? WHERE id=? AND status='running'", (dump(result["sections"]), dump(result["warnings"]), result["characters"], now(), jid))

    def fail_import(self, jid, code, message):
        with self.connection(write=True) as db:
            if self._finish_import_cancel(db, jid):
                return
            db.execute("UPDATE import_jobs SET status='failed',sections_json='[]',characters=0,error_code=?,error_message=?,updated_at=? WHERE id=? AND status='running'", (code, message, now(), jid))

    def _finish_import_cancel(self, db, jid):
        return db.execute("UPDATE import_jobs SET status='canceled',sections_json='[]',warnings_json='[]',characters=0,error_code='',error_message='',updated_at=? WHERE id=? AND status='canceling'", (now(), jid)).rowcount

    def recover_imports(self):
        with self.connection(write=True) as db:
            db.execute("UPDATE import_jobs SET status='interrupted',error_code='IMPORT_INTERRUPTED',error_message='解析进程中断，请人工重试。',updated_at=? WHERE status='running'", (now(),))
            db.execute("UPDATE import_jobs SET status='canceled',sections_json='[]',warnings_json='[]',characters=0,error_code='',error_message='',updated_at=? WHERE status='canceling'", (now(),))

    def retry_import(self, user, jid, payload, key):
        with self.connection(write=True) as db:
            def operation():
                row = self.import_row(db, user, jid)
                if row["status"] not in {"failed", "interrupted", "canceled"} or row["attempts"] >= 3:
                    fail(409, "IMPORT_RETRY_BLOCKED", "当前不能重试；每份文件最多尝试三次，请核对原文件。")
                if self._import_usage(db, user)["pending_count"] >= IMPORT_PENDING_LIMIT:
                    fail(409, "IMPORT_QUOTA", "待确认队列已满，请先处理已有记录，再恢复解析。")
                db.execute("UPDATE import_jobs SET status='queued',error_code='',error_message='',updated_at=? WHERE id=?", (now(), jid))
                self.audit(db, user, "import_retried", jid)
                return {"id": jid, "status": "queued"}
            return self.once(db, user, "import_retry:" + jid, key, payload, operation)

    def commit_import(self, user, jid, payload, key):
        text(payload.get("title"), "资料标题", 120)
        text(payload.get("label", "v1.0"), "版本", 30)
        with self.connection(write=True) as db:
            def operation():
                row = self.import_row(db, user, jid)
                if row["source_id"]:
                    if payload.get("parent_source_id"):
                        parent = self.source(db, user, payload["parent_source_id"])
                        committed = self.source(db, user, row["source_id"])
                        if parent["owner_id"] != user["id"] or self.source_lineage(db, parent)["family_id"] != self.source_lineage(db, committed)["family_id"]:
                            fail(409, "IMPORT_COMMITTED", "此文件已在其他资料中入库，不能重新绑定为新版本。")
                    return {"source_id": row["source_id"], "duplicate": True}
                if row["status"] != "preview":
                    fail(409, "IMPORT_NOT_READY", "解析尚未成功，不能确认入库。")
                sid = self.insert_source(db, user, payload, json.loads(row["sections_json"]))
                db.execute("UPDATE import_jobs SET source_id=?,status='committed',updated_at=? WHERE id=?", (sid, now(), jid))
                self.audit(db, user, "import_confirmed", sid)
                return {"source_id": sid, "duplicate": False}
            return self.once(db, user, "import_commit:" + jid, key, payload, operation)

    def source_file(self, user, sid):
        with self.connection() as db:
            self.source(db, user, sid)
            row = db.execute("SELECT file_bytes,filename FROM import_jobs WHERE source_id=? AND status='committed'", (sid,)).fetchone()
            if not row:
                fail(404, "NOT_FOUND", "该来源没有可下载的原文件。")
            return bytes(row["file_bytes"]), row["filename"]

    def create_source(self, user, payload, key):
        body = text(payload.get("text"), "资料正文", 40000)
        if "parent_source_id" in payload:
            fail(422, "INVALID_INPUT", "请使用资料的新版本入口。")
        with self.connection(write=True) as db:
            def operation():
                sid = self.insert_source(db, user, payload, [{"title": "正文", "text": body}])
                return self.source_view(db, self.source(db, user, sid))
            return self.once(db, user, "source_create", key, payload, operation)

    def request_publication(self, user, sid, payload, key):
        base = version(payload.get("base_version"))
        with self.connection(write=True) as db:
            def operation():
                row = self.source(db, user, sid)
                if row["owner_id"] != user["id"]:
                    fail(403, "OWNER_REQUIRED", "仅资料所有者可以申请发布。")
                if row["version"] != base or row["publication"] == "published":
                    fail(409, "VERSION_CONFLICT", "资料状态已变化，请刷新后申请。")
                scope, shares = self.shared_scope(db, {"scope": "internal", **payload})
                admin_id = text(payload.get("admin_id"), "管理员标识", 80)
                admin = db.execute("SELECT id FROM users WHERE id=? AND role='admin' AND enabled=1", (admin_id,)).fetchone()
                if not admin:
                    fail(422, "INVALID_SCOPE", "请选择管理员和有效共享范围。")
                if db.execute("SELECT 1 FROM publication_requests WHERE source_id=? AND status='pending'", (sid,)).fetchone():
                    fail(409, "ALREADY_PENDING", "此资料已有待处理发布申请。")
                rid = ident("pub")
                db.execute("INSERT INTO publication_requests(id,source_id,admin_id,source_version,scope,shares_json,created_at) VALUES(?,?,?,?,?,?,?)", (rid, sid, admin[0], base, scope, dump(shares if scope == "selected" else []), now()))
                db.execute("INSERT INTO publication_context VALUES(?,?)", (rid, self.source_lineage(db, row)["version"]))
                self.audit(db, user, "publication_requested", sid)
                return {"id": rid, "status": "pending"}
            return self.once(db, user, "publication_request:" + sid, key, payload, operation)

    def admin_publications(self, user):
        if user["role"] != "admin":
            fail(403, "ADMIN_REQUIRED", "需要管理员权限。")
        with self.connection() as db:
            self.require_admin(db, user)
            return [dict(row) for row in db.execute("SELECT p.*,s.title,s.label,u.name AS owner_name FROM publication_requests p JOIN sources s ON s.id=p.source_id JOIN users u ON u.id=s.owner_id WHERE p.admin_id=? AND p.status='pending' ORDER BY p.created_at", (user["id"],))]

    def decide_publication(self, user, rid, payload, key):
        if user["role"] != "admin":
            fail(403, "ADMIN_REQUIRED", "需要管理员权限。")
        decision = payload.get("decision")
        if decision not in {"approved", "rejected"}:
            fail(422, "INVALID_INPUT", "请选择通过或退回。")
        with self.connection(write=True) as db:
            self.require_admin(db, user)
            def operation():
                request = db.execute("SELECT * FROM publication_requests WHERE id=? AND admin_id=?", (rid, user["id"])).fetchone()
                if not request:
                    fail(404, "NOT_FOUND", "发布申请不存在或无权限。")
                row = self.source(db, user, request["source_id"])
                chain = self.source_lineage(db, row)
                context = db.execute("SELECT family_version FROM publication_context WHERE request_id=?", (rid,)).fetchone()
                if request["status"] != "pending" or row["version"] != request["source_version"] or (context and context[0] != chain["version"]):
                    fail(409, "VERSION_CONFLICT", "发布申请或资料版本已变化。")
                if decision == "approved":
                    self.shared_scope(db, {"scope": request["scope"], "shared_user_ids": json.loads(request["shares_json"])})
                db.execute("UPDATE publication_requests SET status=? WHERE id=?", (decision, rid))
                if decision == "approved":
                    db.execute("UPDATE sources SET scope=?,shares_json=?,version=version+1,updated_at=? WHERE publication='published' AND id IN (SELECT source_id FROM source_versions WHERE family_id=?)", (request["scope"], request["shares_json"], now(), chain["family_id"]))
                    db.execute("UPDATE sources SET scope=?,shares_json=?,publication='published',version=version+1,updated_at=? WHERE id=?", (request["scope"], request["shares_json"], now(), row["id"]))
                    self.touch_source_family(db, chain["family_id"], row["id"], True)
                self.audit(db, user, "publication_" + decision, row["id"])
                return {"id": rid, "status": decision}
            return self.once(db, user, "publication_decision:" + rid, key, payload, operation)

    def withdraw_source(self, user, sid, payload, key):
        with self.connection(write=True) as db:
            def operation():
                row = self.source(db, user, sid)
                if user["role"] != "admin" or row["version"] != version(payload.get("base_version")):
                    fail(409 if user["role"] == "admin" else 403, "WITHDRAW_BLOCKED", "需要管理员及当前资料版本。")
                self.require_admin(db, user)
                db.execute("UPDATE sources SET publication='withdrawn',version=version+1,updated_at=? WHERE id=?", (now(), sid))
                chain = self.source_lineage(db, row)
                self.touch_source_family(db, chain["family_id"], replace_current=chain["current_source_id"] == sid)
                self.audit(db, user, "source_withdrawn", sid)
                return {"id": sid, "publication": "withdrawn"}
            return self.once(db, user, "source_withdraw:" + sid, key, payload, operation)

    def content(self, value):
        if isinstance(value, dict) and value.get("schema_version") == 2:
            try:
                return rich_content.validate(value)
            except ValueError as error:
                fail(422, "INVALID_CONTENT", str(error))
        if not isinstance(value, dict) or value.get("schema_version") != 1 or not isinstance(value.get("blocks"), list) or not 1 <= len(value["blocks"]) <= 100:
            fail(422, "INVALID_CONTENT", "正文需要 1 至 100 个文本块。")
        blocks, ids, length = [], set(), 0
        for block in value["blocks"]:
            if not isinstance(block, dict) or block.get("type") not in {"heading", "paragraph", "bullet"}:
                fail(422, "INVALID_CONTENT", "当前支持标题、段落和列表。")
            bid = block.get("id")
            if not isinstance(bid, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", bid) or bid in ids:
                fail(422, "INVALID_CONTENT", "文本块标识无效或重复。")
            body = text(block.get("text", ""), "文本块", 12000, False)
            length += len(body)
            ids.add(bid)
            blocks.append({"id": bid, "type": block["type"], "text": body})
        if length > 50000:
            fail(422, "INVALID_CONTENT", "正文总长度不能超过 50000 字。")
        return {"schema_version": 1, "blocks": blocks}

    def references(self, db, user, refs):
        if not isinstance(refs, list) or len(refs) > 20:
            fail(422, "INVALID_CITATIONS", "引用资料最多 20 份。")
        clean, seen = [], set()
        for ref in refs:
            if not isinstance(ref, dict) or not isinstance(ref.get("source_id"), str):
                fail(422, "SOURCE_UNAVAILABLE", "存在不可用引用，请检查来源。")
            row = db.execute("SELECT * FROM sources WHERE id=?", (ref["source_id"],)).fetchone()
            if not self.readable_source(db, user, row) or row["publication"] != "published" or row["version_id"] != ref.get("version_id"):
                fail(422, "SOURCE_UNAVAILABLE", "引用已撤回、版本不匹配或无访问权限。")
            if row["id"] not in seen:
                clean.append({"source_id": row["id"], "version_id": row["version_id"]})
                seen.add(row["id"])
        return clean

    @staticmethod
    def artifact_user(db, user):
        current = db.execute("SELECT role FROM users WHERE id=? AND enabled=1", (user["id"],)).fetchone()
        if not current or (user.get("role") is not None and current["role"] != user["role"]):
            fail(401, "SESSION_EXPIRED", "账号状态或权限已变化，请重新登录。")

    @staticmethod
    def artifact_sharing_state(db, aid):
        row = db.execute("SELECT * FROM artifact_sharing WHERE artifact_id=?", (aid,)).fetchone()
        return {"scope": row["scope"], "shared_user_ids": json.loads(row["shares_json"]), "version": row["version"]} if row else {"scope": "private", "shared_user_ids": [], "version": 1}

    @staticmethod
    def artifact_shared(user, sharing):
        return sharing["scope"] == "internal" or (sharing["scope"] == "selected" and user["id"] in sharing["shared_user_ids"])

    def artifact_access(self, db, user, row, revision=None):
        requested = revision if revision is not None else row["revision"]
        if row["owner_id"] == user["id"]:
            return "owner"
        if db.execute("SELECT 1 FROM reviews WHERE artifact_id=? AND revision=? AND reviewer_id=?", (row["id"], requested, user["id"])).fetchone():
            return "reviewer"
        sharing = self.artifact_sharing_state(db, row["id"])
        if requested == row["revision"] and self.artifact_shared(user, sharing):
            return "shared"
        return None

    def artifact_row(self, db, user, aid, revision=None):
        self.artifact_user(db, user)
        row = db.execute("SELECT * FROM artifacts WHERE id=?", (aid,)).fetchone()
        if not row or not self.artifact_access(db, user, row, revision):
            fail(404, "NOT_FOUND", "文档不存在或无访问权限。")
        return row

    def artifact_view(self, db, user, row, rev=None):
        result = dict(row)
        revision = db.execute("SELECT * FROM revisions WHERE artifact_id=? AND revision=?", (row["id"], rev or row["revision"])).fetchone()
        if not revision:
            fail(404, "NOT_FOUND", "修订不存在。")
        current = revision["revision"] == row["revision"]
        result.update({"title": revision["title"], "revision": revision["revision"], "current_revision": row["revision"], "content_json": json.loads(revision["content_json"]), "citations": json.loads(revision["citations_json"]), "can_edit": row["owner_id"] == user["id"] and current})
        if not current:
            result["status"] = "historical"
        access = self.artifact_access(db, user, row, revision["revision"])
        sharing = self.artifact_sharing_state(db, row["id"])
        result.update(access=access, is_shared=access != "owner" and self.artifact_shared(user, sharing), can_share=access == "owner" and current, can_comment=access in {"owner", "reviewer"} and current, sharing=sharing if access == "owner" else {"scope": sharing["scope"]})
        details = []
        for ref in result["citations"]:
            src = db.execute("SELECT * FROM sources WHERE id=?", (ref["source_id"],)).fetchone()
            readable = self.readable_source(db, user, src)
            details.append({**ref, "title": src["title"] if readable else "无权访问的来源", "label": src["label"] if readable else "", "available": bool(readable and src["publication"] == "published" and src["version_id"] == ref["version_id"])})
        result["reference_details"] = details
        result["can_export"] = bool(current and access in {"owner", "reviewer"} and row["status"] == "approved" and all(ref["available"] for ref in details) and db.execute("SELECT 1 FROM reviews WHERE artifact_id=? AND revision=? AND status='approved'", (row["id"], revision["revision"])).fetchone())
        result["template_binding"] = self.artifact_template(db, row["id"])
        result["fields"] = self.artifact_fields(db, row["id"], revision["revision"])
        result["checks"] = writing.checks(result["template_binding"], result["fields"], result["content_json"], [ref["source_id"] for ref in details if not ref["available"]])
        undo = db.execute("SELECT id,applied_revision FROM writing_suggestions WHERE artifact_id=? AND owner_id=? AND status='applied' AND applied_revision=?", (row["id"], user["id"], revision["revision"])).fetchone() if current else None
        result["undo_suggestion"] = dict(undo) if undo else None
        owner = row["owner_id"] == user["id"]
        result["reviews"] = [dict(item) for item in db.execute("SELECT r.*,u.name AS reviewer_name FROM reviews r JOIN users u ON u.id=r.reviewer_id WHERE artifact_id=? AND (? OR (r.revision=? AND r.reviewer_id=?)) ORDER BY created_at DESC,r.rowid DESC", (row["id"], owner, revision["revision"], user["id"]))]
        result["comments"] = [dict(item) for item in db.execute("SELECT c.id,c.revision,c.text,c.created_at,u.name AS author_name FROM comments c JOIN users u ON u.id=c.author_id WHERE artifact_id=? AND (? OR c.revision=?) ORDER BY c.created_at,c.rowid", (row["id"], owner, revision["revision"]))] if access in {"owner", "reviewer"} else []
        result["history"] = [dict(item) for item in db.execute("SELECT revision,title,created_at FROM revisions WHERE artifact_id=? ORDER BY revision DESC LIMIT 30", (row["id"],)) if row["owner_id"] == user["id"] or db.execute("SELECT 1 FROM reviews WHERE artifact_id=? AND revision=? AND reviewer_id=?", (row["id"], item["revision"], user["id"])).fetchone()] if access != "shared" else []
        if owner and current:
            copy = self.working_copy_row(db, row["id"])
            result.update(draft_version=copy["version"] if copy else 0, has_working_copy=bool(copy and copy["payload_json"]))
        return result

    def list_artifacts(self, user):
        with self.connection() as db:
            db.execute("BEGIN")
            self.artifact_user(db, user)
            result = []
            for row in db.execute("SELECT a.*,u.name AS owner_name FROM artifacts a JOIN users u ON u.id=a.owner_id ORDER BY a.updated_at DESC,a.id"):
                access = self.artifact_access(db, user, row)
                if access:
                    sharing = self.artifact_sharing_state(db, row["id"])
                    result.append({**dict(row), "access": access, "scope": sharing["scope"], "is_shared": access != "owner" and self.artifact_shared(user, sharing)})
                    if len(result) >= 100:
                        break
            return result

    def get_artifact(self, user, aid, rev=None):
        with self.connection() as db:
            db.execute("BEGIN")
            row = self.artifact_row(db, user, aid, rev)
            return self.artifact_view(db, user, row, rev)

    def share_artifact(self, user, aid, payload, key):
        base, revision = version(payload.get("base_version")), version(payload.get("base_revision"))
        if not set(payload).issubset({"base_version", "base_revision", "scope", "shared_user_ids", "confirm_body"}):
            fail(422, "INVALID_INPUT", "共享请求包含不支持的字段。")
        if not isinstance(payload.get("scope"), str):
            fail(422, "INVALID_SCOPE", "请选择有效的共享范围。")
        with self.connection(write=True) as db:
            def operation():
                row = self.artifact_row(db, user, aid)
                if row["owner_id"] != user["id"]:
                    fail(403, "OWNER_REQUIRED", "仅稿件所有者可以调整共享。")
                if self.artifact_sharing_state(db, aid)["version"] != base:
                    fail(409, "VERSION_CONFLICT", "共享范围已变化，请重新打开共享设置。")
                if row["revision"] != revision:
                    fail(409, "REVISION_CONFLICT", "正文修订已变化，请读取最新稿件后重新确认共享。")
                scope, shares = self.shared_scope(db, payload)
                if user["id"] in shares:
                    fail(422, "INVALID_SCOPE", "所有者已有访问权，请选择其他同事。")
                if scope != "private" and payload.get("confirm_body") is not True:
                    fail(422, "BODY_CONFIRMATION_REQUIRED", "请确认共享正文、文档信息及后续修订；引用资料不会自动授权。")
                db.execute("INSERT INTO artifact_sharing VALUES(?,?,?,?,?) ON CONFLICT(artifact_id) DO UPDATE SET scope=excluded.scope,shares_json=excluded.shares_json,version=excluded.version,updated_at=excluded.updated_at", (aid, scope, dump(shares), base + 1, now()))
                self.audit(db, user, "artifact_sharing_changed", aid)
                return {"id": aid, "applied_version": base + 1}
            result = self.once(db, user, "artifact_share:" + aid, key, payload, operation)
        current = self.get_artifact(user, result["id"])
        current["sharing_result"] = {"applied_version": result["applied_version"], "is_current": current["sharing"]["version"] == result["applied_version"]}
        return current

    @staticmethod
    def template_view(row):
        result = dict(row)
        result["fields"] = json.loads(result.pop("fields_json"))
        result["sections"] = json.loads(result.pop("sections_json"))
        if "active" in result:
            result["active"] = bool(result["active"])
        return result

    def templates(self, include_inactive=False):
        with self.connection() as db:
            return [self.template_view(row) for row in db.execute("SELECT v.*,t.active FROM templates t JOIN template_versions v ON v.template_id=t.id AND v.number=t.current_version WHERE t.active=1 OR ? ORDER BY t.id", (include_inactive,))]

    def template_version(self, vid):
        with self.connection() as db:
            row = db.execute("SELECT * FROM template_versions WHERE id=?", (vid,)).fetchone()
            if not row:
                fail(404, "NOT_FOUND", "模板版本不存在。")
            return self.template_view(row)

    def admin_templates(self, user):
        if user["role"] != "admin":
            fail(403, "ADMIN_REQUIRED", "需要管理员权限。")
        with self.connection() as db:
            self.require_admin(db, user)
            result = self.templates(include_inactive=True)
            for item in result:
                item["history"] = [dict(row) for row in db.execute("SELECT id,number,name,created_at FROM template_versions WHERE template_id=? ORDER BY number DESC", (item["template_id"],))]
            return result

    def update_template(self, user, tid, payload, key):
        if user["role"] != "admin":
            fail(403, "ADMIN_REQUIRED", "需要管理员权限。")
        try:
            definition = writing.definition(payload)
        except ValueError as error:
            fail(422, "INVALID_TEMPLATE", str(error))
        base = version(payload.get("base_version"))
        with self.connection(write=True) as db:
            self.require_admin(db, user)
            def operation():
                row = db.execute("SELECT * FROM templates WHERE id=?", (tid,)).fetchone()
                if not row:
                    fail(404, "NOT_FOUND", "模板不存在。")
                if row["current_version"] != base:
                    fail(409, "TEMPLATE_CONFLICT", "模板已更新，请重新读取后编辑。")
                vid, number = ident("tv"), base + 1
                db.execute("INSERT INTO template_versions VALUES(?,?,?,?,?,?,?,?)", (vid, tid, number, definition["name"], dump(definition["fields"]), dump(definition["sections"]), now(), user["id"]))
                db.execute("UPDATE templates SET current_version=?,active=? WHERE id=?", (number, int(definition["active"]), tid))
                self.audit(db, user, "template_version_created", vid)
                return {"id": vid, "template_id": tid, "number": number, "active": definition["active"]}
            return self.once(db, user, "template_update:" + tid, key, payload, operation)

    def artifact_template(self, db, aid):
        row = db.execute("SELECT v.* FROM artifact_templates a JOIN template_versions v ON v.id=a.template_version_id WHERE a.artifact_id=?", (aid,)).fetchone()
        return self.template_view(row) if row else None

    @staticmethod
    def artifact_fields(db, aid, revision):
        row = db.execute("SELECT values_json FROM revision_fields WHERE artifact_id=? AND revision=?", (aid, revision)).fetchone()
        return json.loads(row[0]) if row else {}

    @staticmethod
    def field_values(template, data):
        try:
            return writing.values(template, data)
        except ValueError as error:
            fail(422, "INVALID_FIELDS", str(error))

    def create_artifact(self, user, payload, key):
        title = text(payload.get("title"), "文档标题", 120)
        template = payload.get("template", "solution")
        if not isinstance(template, str) or template not in {"solution", "delivery"}:
            fail(422, "INVALID_TEMPLATE", "模板无效。")
        ids = payload.get("source_ids", [])
        if not isinstance(ids, list) or len(ids) > 8 or any(not isinstance(sid, str) for sid in ids):
            fail(422, "SOURCE_REQUIRED", "引用资料须为不超过8份已发布资料的列表，也可不选资料起草。")
        notes = text(payload.get("notes", ""), "补充信息", 2000, False)
        with self.connection(write=True) as db:
            def operation():
                vid = payload.get("template_version_id")
                if vid is not None and not isinstance(vid, str):
                    fail(422, "INVALID_TEMPLATE", "模板版本无效。")
                selected = db.execute("SELECT v.* FROM templates t JOIN template_versions v ON v.template_id=t.id AND v.number=t.current_version WHERE t.active=1 AND ((? IS NOT NULL AND v.id=?) OR (? IS NULL AND t.id=?))", (vid, vid, vid, template)).fetchone()
                if not selected:
                    fail(409, "TEMPLATE_UNAVAILABLE", "所选模板已更新或停用，请重新选择当前版本。")
                definition = self.template_view(selected)
                values = self.field_values(definition, payload.get("fields", {}))
                refs, blocks = [], writing.draft_blocks(definition, values, notes, ident)
                for index, sid in enumerate(dict.fromkeys(ids), 1):
                    row = self.source(db, user, sid, True, current=True)
                    refs.append({"source_id": sid, "version_id": row["version_id"]})
                    blocks.append({"id": ident("b"), "type": "heading", "text": "参考资料 " + row["title"]})
                    for section in json.loads(row["sections_json"])[:2]:
                        blocks.append({"id": ident("b"), "type": "paragraph", "text": f"{section['text'][:1200]} [{index}]"})
                body = self.content({"schema_version": 1, "blocks": blocks})
                aid, stamp = ident("doc"), now()
                db.execute("INSERT INTO artifacts VALUES(?,?,?,?,?,?,?,?)", (aid, user["id"], title, definition["template_id"], 1, "draft", stamp, stamp))
                db.execute("INSERT INTO revisions VALUES(?,?,?,?,?,?,?)", (aid, 1, title, dump(body), dump(refs), user["id"], stamp))
                db.execute("INSERT INTO artifact_templates VALUES(?,?)", (aid, definition["id"]))
                db.execute("INSERT INTO revision_fields VALUES(?,?,?)", (aid, 1, dump(values)))
                self.audit(db, user, "artifact_created", aid)
                return {"id": aid, "revision": 1, "mode": "offline_template", "template_version_id": definition["id"], "missing_fields": writing.checks(definition, values, body, [])["missing_fields"]}
            return self.once(db, user, "artifact_create", key, payload, operation)

    @staticmethod
    def working_copy_row(db, aid):
        return db.execute("SELECT * FROM artifact_working_copies WHERE artifact_id=?", (aid,)).fetchone()

    @staticmethod
    def draft_version(value):
        if type(value) is not int or value < 0:
            fail(422, "INVALID_DRAFT_VERSION", "缺少有效的工作草稿版本号。")
        return value

    def working_copy_owner(self, db, user, aid):
        row = self.artifact_row(db, user, aid)
        if row["owner_id"] != user["id"]:
            fail(404, "NOT_FOUND", "工作草稿不存在或无访问权限。")
        return row

    def require_clean_working_copy(self, db, aid):
        copy = self.working_copy_row(db, aid)
        if copy and copy["payload_json"]:
            fail(409, "DRAFT_PENDING", "请先将工作草稿保存为修订，或明确放弃草稿后继续。")

    def get_working_copy(self, user, aid):
        with self.connection() as db:
            db.execute("BEGIN")
            row = self.working_copy_owner(db, user, aid)
            copy = self.working_copy_row(db, aid)
            payload = json.loads(copy["payload_json"]) if copy and copy["payload_json"] else None
            result = {"id": aid, "draft_version": copy["version"] if copy else 0, "base_revision": copy["base_revision"] if copy else row["revision"], "current_revision": row["revision"], "has_draft": payload is not None, "stale": bool(payload and copy["base_revision"] != row["revision"]), "updated_at": copy["updated_at"] if copy else None, "draft": payload}
            if payload:
                details = []
                for ref in payload["citations"]:
                    src = db.execute("SELECT * FROM sources WHERE id=?", (ref["source_id"],)).fetchone()
                    readable = self.readable_source(db, user, src)
                    details.append({**ref, "title": src["title"] if readable else "无权访问的来源", "label": src["label"] if readable else "", "available": bool(readable and src["publication"] == "published" and src["version_id"] == ref["version_id"])})
                result["reference_details"] = details
            return result

    def save_working_copy(self, user, aid, payload, key):
        title, body = text(payload.get("title"), "文档标题", 120), self.content(payload.get("content_json"))
        base, expected = version(payload.get("base_revision")), self.draft_version(payload.get("base_draft_version"))
        with self.connection(write=True) as db:
            row = self.working_copy_owner(db, user, aid)
            def operation():
                if row["revision"] != base:
                    fail(409, "REVISION_CONFLICT", "正式修订已变化，工作草稿未覆盖，请保留本页并检查最新稿件。")
                copy = self.working_copy_row(db, aid)
                if (copy["version"] if copy else 0) != expected:
                    fail(409, "DRAFT_CONFLICT", "工作草稿已在其他页面更新，本页未覆盖，请读取最新草稿后处理。")
                previous = json.loads(db.execute("SELECT content_json FROM revisions WHERE artifact_id=? AND revision=?", (aid, base)).fetchone()[0])
                working = json.loads(copy["payload_json"])["content_json"] if copy and copy["payload_json"] else previous
                if (previous.get("schema_version") == 2 or working.get("schema_version") == 2) and body.get("schema_version") != 2:
                    fail(409, "FORMAT_DOWNGRADE", "不能用旧文本格式覆盖结构化稿件。")
                refs = self.references(db, user, payload.get("citations", []))
                values = self.field_values(self.artifact_template(db, aid), payload.get("fields", self.artifact_fields(db, aid, base)))
                stamp, number = now(), expected + 1
                data = {"title": title, "content_json": body, "citations": refs, "fields": values}
                db.execute("INSERT INTO artifact_working_copies VALUES(?,?,?,?,?) ON CONFLICT(artifact_id) DO UPDATE SET version=excluded.version,base_revision=excluded.base_revision,payload_json=excluded.payload_json,updated_at=excluded.updated_at", (aid, number, base, dump(data), stamp))
                return {"id": aid, "draft_version": number, "base_revision": base, "updated_at": stamp}
            result = self.once(db, user, "working_copy_save:" + aid, key, payload, operation)
            copy = self.working_copy_row(db, aid)
            if not copy or copy["version"] != result["draft_version"] or row["revision"] != result["base_revision"]:
                fail(409, "DRAFT_CONFLICT", "此次同步此前已完成，但草稿随后已变化；未重放旧内容。")
            return result

    def discard_working_copy(self, user, aid, payload, key):
        expected = self.draft_version(payload.get("base_draft_version"))
        if payload.get("confirm") is not True:
            fail(422, "CONFIRM_REQUIRED", "请明确确认放弃工作草稿；正式修订不受影响。")
        with self.connection(write=True) as db:
            row = self.working_copy_owner(db, user, aid)
            def operation():
                copy = self.working_copy_row(db, aid)
                if (copy["version"] if copy else 0) != expected:
                    fail(409, "DRAFT_CONFLICT", "草稿已变化，不能按旧确认放弃新的内容。")
                stamp, number = now(), expected + 1
                db.execute("INSERT INTO artifact_working_copies VALUES(?,?,?,NULL,?) ON CONFLICT(artifact_id) DO UPDATE SET version=excluded.version,base_revision=excluded.base_revision,payload_json=NULL,updated_at=excluded.updated_at", (aid, number, row["revision"], stamp))
                self.audit(db, user, "working_copy_discarded", aid)
                return {"id": aid, "draft_version": number, "has_draft": False}
            result = self.once(db, user, "working_copy_discard:" + aid, key, payload, operation)
            current = self.working_copy_row(db, aid)
            return {**result, "is_current": current["version"] == result["draft_version"] and current["payload_json"] is None}

    def persist_revision(self, db, user, row, title, body, refs, values, working_copy=False):
        if not working_copy:
            self.require_clean_working_copy(db, row["id"])
        stamp, revision = now(), row["revision"] + 1
        db.execute("INSERT INTO revisions VALUES(?,?,?,?,?,?,?)", (row["id"], revision, title, dump(body), dump(refs), user["id"], stamp))
        db.execute("INSERT INTO revision_fields VALUES(?,?,?)", (row["id"], revision, dump(values)))
        db.execute("UPDATE artifacts SET title=?,revision=?,status='draft',updated_at=? WHERE id=?", (title, revision, stamp, row["id"]))
        db.execute("UPDATE reviews SET status='superseded' WHERE artifact_id=? AND status='pending'", (row["id"],))
        db.execute("UPDATE writing_suggestions SET status='superseded' WHERE artifact_id=? AND status='prepared'", (row["id"],))
        db.execute("UPDATE artifact_working_copies SET version=version+1,base_revision=?,payload_json=NULL,updated_at=? WHERE artifact_id=?", (revision, stamp, row["id"]))
        self.audit(db, user, "artifact_saved", row["id"])
        copy = self.working_copy_row(db, row["id"])
        return {"id": row["id"], "revision": revision, "status": "draft", "draft_version": copy["version"] if copy else 0}

    def save_artifact(self, user, aid, payload, key):
        title = text(payload.get("title"), "文档标题", 120)
        body, base = self.content(payload.get("content_json")), version(payload.get("base_revision"))
        with self.connection(write=True) as db:
            def operation():
                row = self.artifact_row(db, user, aid)
                if row["owner_id"] != user["id"]:
                    fail(403, "READ_ONLY", "共享文档只读；仅作者可以修改。")
                if row["revision"] != base:
                    fail(409, "REVISION_CONFLICT", "文档已被更新，请保留当前编辑并检查最新版本。")
                previous = json.loads(db.execute("SELECT content_json FROM revisions WHERE artifact_id=? AND revision=?", (aid, base)).fetchone()[0])
                if previous.get("schema_version") == 2 and body.get("schema_version") != 2:
                    fail(409, "FORMAT_DOWNGRADE", "不能用旧文本格式覆盖结构化稿件，请使用支持该格式的编辑器。")
                refs = self.references(db, user, payload.get("citations", []))
                values = self.field_values(self.artifact_template(db, aid), payload.get("fields", self.artifact_fields(db, aid, base)))
                copy = self.working_copy_row(db, aid)
                if copy and copy["payload_json"] and json.loads(copy["payload_json"])["content_json"].get("schema_version") == 2 and body.get("schema_version") != 2:
                    fail(409, "FORMAT_DOWNGRADE", "不能用旧文本格式覆盖结构化工作草稿。")
                if "base_draft_version" in payload:
                    expected = self.draft_version(payload["base_draft_version"])
                    if expected != (copy["version"] if copy else 0):
                        fail(409, "DRAFT_CONFLICT", "工作草稿已变化，本次修订未保存，请重新核对。")
                else:
                    self.require_clean_working_copy(db, aid)
                return self.persist_revision(db, user, row, title, body, refs, values, working_copy=True)
            return self.once(db, user, "artifact_save:" + aid, key, payload, operation)

    def suggestion_row(self, db, user, aid, sid):
        row = db.execute("SELECT * FROM writing_suggestions WHERE id=? AND artifact_id=? AND owner_id=?", (sid, aid, user["id"])).fetchone()
        if not row:
            fail(404, "NOT_FOUND", "写作建议不存在或无访问权限。")
        artifact = self.artifact_row(db, user, aid)
        if artifact["owner_id"] != user["id"]:
            fail(404, "NOT_FOUND", "写作建议不存在或无访问权限。")
        return row, artifact

    @staticmethod
    def prepared_suggestion(row):
        if row["status"] == "canceled":
            fail(409, "SUGGESTION_CANCELED", "本次建议已取消，请重新预览。")
        if row["status"] != "prepared":
            fail(409, "SUGGESTION_CHANGED", "正文或建议状态已变化，请重新预览。")
        if row["expires"] < time.time():
            fail(409, "SUGGESTION_EXPIRED", "本次建议已过期，请重新预览。")

    def suggestion_source(self, db, user, row):
        ref = json.loads(row["source_ref_json"])
        source = self.source(db, user, ref["source_id"], True, current=True)
        sections = json.loads(source["sections_json"])
        index = ref["section_index"]
        if source["version_id"] != ref["version_id"] or index >= len(sections) or fingerprint(sections[index]) != ref["section_hash"]:
            fail(409, "SUGGESTION_CHANGED", "建议来源的版本或内容已变化，请重新预览。")
        return source, sections[index], ref

    def create_suggestion(self, user, aid, payload, key):
        base = version(payload.get("base_revision"))
        target_id, sid = text(payload.get("target_block_id"), "目标段落", 80), text(payload.get("source_id"), "来源", 80)
        operation, index = payload.get("operation", "insert_after"), payload.get("section_index")
        if operation not in ("insert_after", "replace") or type(index) is not int or index < 0:
            fail(422, "INVALID_TARGET", "请选择插入/替换方式及有效的来源章节。")
        with self.connection(write=True) as db:
            def make_preview():
                artifact = self.artifact_row(db, user, aid)
                if artifact["owner_id"] != user["id"]:
                    fail(403, "READ_ONLY", "仅作者可以准备写作建议。")
                if artifact["revision"] != base:
                    fail(409, "SUGGESTION_CHANGED", "文档修订已变化，请重新读取后预览。")
                self.require_clean_working_copy(db, aid)
                revision = db.execute("SELECT content_json FROM revisions WHERE artifact_id=? AND revision=?", (aid, base)).fetchone()
                target = next((block for block in json.loads(revision[0])["blocks"] if block["id"] == target_id), None)
                if not target or (operation == "replace" and target["type"] in {"heading", "table"}):
                    fail(422, "INVALID_TARGET", "目标段落不存在，或不允许替换章节标题。")
                source = self.source(db, user, sid, True, current=True)
                sections = json.loads(source["sections_json"])
                if index >= len(sections):
                    fail(422, "INVALID_TARGET", "来源章节不存在。")
                ref = {"source_id": sid, "version_id": source["version_id"], "section_index": index, "section_hash": fingerprint(sections[index])}
                suggestion_id = ident("suggestion")
                db.execute("INSERT INTO writing_suggestions(id,artifact_id,owner_id,base_revision,target_block_id,target_hash,source_ref_json,operation,expires,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)", (suggestion_id, aid, user["id"], base, target_id, fingerprint(target), dump(ref), operation, time.time() + 600, now()))
                self.audit(db, user, "writing_suggestion_prepared", suggestion_id)
                return {"id": suggestion_id}
            result = self.once(db, user, "writing_preview:" + aid, key, payload, make_preview)
        # Replayed preview responses must re-check current source permissions, not echo cached text.
        return self.get_suggestion(user, aid, result["id"])

    def get_suggestion(self, user, aid, sid):
        with self.connection() as db:
            row, artifact = self.suggestion_row(db, user, aid, sid)
            self.prepared_suggestion(row)
            if artifact["revision"] != row["base_revision"]:
                fail(409, "SUGGESTION_CHANGED", "文档修订已变化，请重新预览。")
            revision = db.execute("SELECT content_json FROM revisions WHERE artifact_id=? AND revision=?", (aid, row["base_revision"])).fetchone()
            target = next((block for block in json.loads(revision[0])["blocks"] if block["id"] == row["target_block_id"]), None)
            if not target or fingerprint(target) != row["target_hash"]:
                fail(409, "SUGGESTION_CHANGED", "目标段落已变化，请重新预览。")
            source, section, ref = self.suggestion_source(db, user, row)
            return {"id": sid, "artifact_id": aid, "status": "prepared", "base_revision": row["base_revision"], "target_block_id": row["target_block_id"], "target_hash": row["target_hash"], "target_text": target["text"], "target_format_reset": json.loads(revision[0]).get("schema_version") == 2 and row["operation"] == "replace", "operation": row["operation"], "source_fingerprint": ref["section_hash"], "source": {"source_id": source["id"], "version_id": source["version_id"], "title": source["title"], "label": source["label"], "locator": source_locator(section, ref["section_index"])}, "excerpt": section["text"][:1200], "expires_at": datetime.fromtimestamp(row["expires"], timezone.utc).isoformat(), "mode": "offline_excerpt"}

    def apply_suggestion(self, user, aid, sid, payload, key):
        base = version(payload.get("base_revision"))
        with self.connection(write=True) as db:
            def operation():
                row, artifact = self.suggestion_row(db, user, aid, sid)
                if row["status"] == "applied":
                    return {"id": aid, "suggestion_id": sid, "revision": row["applied_revision"], "status": "draft"}
                self.prepared_suggestion(row)
                if artifact["revision"] != base or row["base_revision"] != base:
                    fail(409, "SUGGESTION_CHANGED", "文档修订已变化，请重新预览。")
                revision = db.execute("SELECT * FROM revisions WHERE artifact_id=? AND revision=?", (aid, base)).fetchone()
                body = json.loads(revision["content_json"])
                target = next((block for block in body["blocks"] if block["id"] == row["target_block_id"]), None)
                if not target or fingerprint(target) != row["target_hash"]:
                    fail(409, "SUGGESTION_CHANGED", "目标段落已变化，请重新预览。")
                source, section, _ = self.suggestion_source(db, user, row)
                candidate_refs = json.loads(revision["citations_json"])
                if not any(ref["source_id"] == source["id"] for ref in candidate_refs):
                    candidate_refs.append({"source_id": source["id"], "version_id": source["version_id"]})
                refs = self.references(db, user, candidate_refs)
                number = next(index for index, ref in enumerate(refs, 1) if ref["source_id"] == source["id"])
                quote = section["text"][:1200] + f" [{number}]"
                if row["operation"] == "replace":
                    rich_content.replace_text(target, quote)
                else:
                    position = body["blocks"].index(target) + 1
                    body["blocks"].insert(position, rich_content.quote_block(ident("b"), quote, body["schema_version"]))
                values = self.artifact_fields(db, aid, base)
                result = self.persist_revision(db, user, artifact, revision["title"], self.content(body), refs, values)
                db.execute("UPDATE writing_suggestions SET status='applied',applied_revision=? WHERE id=?", (result["revision"], sid))
                self.audit(db, user, "writing_suggestion_applied", sid)
                return {**result, "suggestion_id": sid}
            return self.once(db, user, "writing_apply:" + sid, key, payload, operation)

    def cancel_suggestion(self, user, aid, sid, payload, key):
        with self.connection(write=True) as db:
            def operation():
                row, _ = self.suggestion_row(db, user, aid, sid)
                if row["status"] in ("applied", "undone"):
                    fail(409, "SUGGESTION_CHANGED", "建议已经采纳，不能用取消代替撤销。")
                db.execute("UPDATE writing_suggestions SET status='canceled' WHERE id=?", (sid,))
                self.audit(db, user, "writing_suggestion_canceled", sid)
                return {"id": sid, "status": "canceled"}
            return self.once(db, user, "writing_cancel:" + sid, key, payload, operation)

    def undo_suggestion(self, user, aid, sid, payload, key):
        base = version(payload.get("base_revision"))
        with self.connection(write=True) as db:
            def operation():
                row, artifact = self.suggestion_row(db, user, aid, sid)
                if row["status"] == "undone":
                    return {"id": aid, "revision": row["undo_revision"], "status": "draft"}
                if row["status"] != "applied" or artifact["revision"] != base or row["applied_revision"] != base:
                    fail(409, "SUGGESTION_CHANGED", "采纳后已有其他修订，不能撤销并覆盖后续修改。")
                previous = db.execute("SELECT * FROM revisions WHERE artifact_id=? AND revision=?", (aid, row["base_revision"])).fetchone()
                refs = self.references(db, user, json.loads(previous["citations_json"]))
                result = self.persist_revision(db, user, artifact, previous["title"], json.loads(previous["content_json"]), refs, self.artifact_fields(db, aid, row["base_revision"]))
                db.execute("UPDATE writing_suggestions SET status='undone',undo_revision=? WHERE id=?", (result["revision"], sid))
                self.audit(db, user, "writing_suggestion_undone", sid)
                return result
            return self.once(db, user, "writing_undo:" + sid, key, payload, operation)

    def submit_review(self, user, aid, payload, key):
        base = version(payload.get("base_revision"))
        with self.connection(write=True) as db:
            def operation():
                row = self.artifact_row(db, user, aid)
                if row["owner_id"] != user["id"] or payload.get("reviewer_id") == user["id"]:
                    fail(403, "SELF_REVIEW_FORBIDDEN", "仅作者提交，且审核人必须是另一位同事。")
                if row["revision"] != base or row["status"] == "pending":
                    fail(409, "REVISION_CONFLICT", "修订已变化或已有待处理审核。")
                self.require_clean_working_copy(db, aid)
                reviewer_id = text(payload.get("reviewer_id"), "审核同事标识", 80)
                reviewer = db.execute("SELECT id FROM users WHERE id=? AND enabled=1", (reviewer_id,)).fetchone()
                if not reviewer:
                    fail(422, "REVIEWER_REQUIRED", "请选择有效审核同事。")
                rev = db.execute("SELECT * FROM revisions WHERE artifact_id=? AND revision=?", (aid, base)).fetchone()
                refs = json.loads(rev["citations_json"])
                self.references(db, user, refs)
                self.references(db, {"id": reviewer[0]}, refs)
                diagnostic = writing.checks(self.artifact_template(db, aid), self.artifact_fields(db, aid, base), json.loads(rev["content_json"]), [])
                if diagnostic["missing_fields"]:
                    fail(422, "REQUIRED_FIELDS", "请先补齐并保存模板必填信息：" + "、".join(field["label"] for field in diagnostic["missing_fields"]))
                if sum(len(item["text"]) for item in json.loads(rev["content_json"])["blocks"]) < 20:
                    fail(422, "CONTENT_TOO_SHORT", "请先补充文档正文。")
                rid = ident("review")
                db.execute("INSERT INTO reviews(id,artifact_id,revision,reviewer_id,created_at) VALUES(?,?,?,?,?)", (rid, aid, base, reviewer[0], now()))
                db.execute("UPDATE artifacts SET status='pending',updated_at=? WHERE id=?", (now(), aid))
                self.audit(db, user, "review_submitted", aid)
                return {"id": rid, "artifact_id": aid, "revision": base, "status": "pending"}
            return self.once(db, user, "review_submit:" + aid, key, payload, operation)

    def reviews(self, user):
        with self.connection() as db:
            return [dict(row) for row in db.execute("SELECT r.*,a.title,u.name AS author_name FROM reviews r JOIN artifacts a ON a.id=r.artifact_id JOIN users u ON u.id=a.owner_id WHERE r.reviewer_id=? AND r.status='pending' AND a.revision=r.revision ORDER BY r.created_at", (user["id"],))]

    def decide_review(self, user, rid, payload, key):
        decision, note = payload.get("decision"), text(payload.get("note", ""), "审核意见", 2000, False)
        if decision not in {"approved", "rejected"} or (decision == "rejected" and not note):
            fail(422, "REVIEW_NOTE_REQUIRED", "请选择结果；退回时请填写意见。")
        with self.connection(write=True) as db:
            def operation():
                review = db.execute("SELECT * FROM reviews WHERE id=? AND reviewer_id=?", (rid, user["id"])).fetchone()
                if not review:
                    fail(404, "NOT_FOUND", "审核不存在或未分派给你。")
                row = db.execute("SELECT * FROM artifacts WHERE id=?", (review["artifact_id"],)).fetchone()
                if not row:
                    fail(404, "NOT_FOUND", "被审文档不存在。")
                if row["owner_id"] == user["id"]:
                    fail(403, "SELF_REVIEW_FORBIDDEN", "作者不能自审。")
                if review["status"] != "pending" or row["revision"] != review["revision"] or row["revision"] != version(payload.get("revision")):
                    fail(409, "REVIEW_SUPERSEDED", "被审修订已失效，请作者重新提交。")
                rev = db.execute("SELECT citations_json FROM revisions WHERE artifact_id=? AND revision=?", (row["id"], row["revision"])).fetchone()
                self.references(db, user, json.loads(rev[0]))
                db.execute("UPDATE reviews SET status=?,note=?,decided_at=? WHERE id=?", (decision, note, now(), rid))
                db.execute("UPDATE artifacts SET status=?,updated_at=? WHERE id=?", (decision, now(), row["id"]))
                self.audit(db, user, "review_" + decision, row["id"])
                return {"id": rid, "revision": row["revision"], "status": decision}
            return self.once(db, user, "review_decision:" + rid, key, payload, operation)

    def comment(self, user, aid, payload, key):
        body = text(payload.get("text"), "评论", 2000)
        base = version(payload.get("base_revision"))
        with self.connection(write=True) as db:
            def operation():
                row = self.artifact_row(db, user, aid, base)
                if self.artifact_access(db, user, row, base) == "shared":
                    fail(403, "READ_ONLY", "共享稿件只读；留言仅向作者和指定审核同事开放。")
                if row["revision"] != base:
                    fail(409, "REVISION_CONFLICT", "文档修订已变化，请重新读取后留言。")
                cid = ident("comment")
                db.execute("INSERT INTO comments(id,artifact_id,revision,author_id,text,created_at) VALUES(?,?,?,?,?,?)", (cid, aid, base, user["id"], body, now()))
                self.audit(db, user, "comment_added", aid)
                return {"id": cid}
            return self.once(db, user, "comment:" + aid, key, payload, operation)

    def exportable(self, db, user, aid, revision):
        row = self.artifact_row(db, user, aid)
        if self.artifact_access(db, user, row) == "shared":
            fail(403, "READ_ONLY", "共享稿件仅可阅读，不授予 Word 导出权限。")
        if row["revision"] != revision or row["status"] != "approved" or not db.execute("SELECT 1 FROM reviews WHERE artifact_id=? AND revision=? AND status='approved'", (aid, revision)).fetchone():
            fail(409, "APPROVAL_REQUIRED", "仅当前已通过审核的修订可以导出。")
        rev = db.execute("SELECT * FROM revisions WHERE artifact_id=? AND revision=?", (aid, revision)).fetchone()
        self.references(db, user, json.loads(rev["citations_json"]))
        return row, rev

    def create_export(self, user, aid, payload, key):
        revision = version(payload.get("revision"))
        with self.connection(write=True) as db:
            def operation():
                row, rev = self.exportable(db, user, aid, revision)
                from word_export import render_word
                refs = [self.source_view(db, self.source(db, user, ref["source_id"], True)) for ref in json.loads(rev["citations_json"])]
                output = render_word(row["title"], json.loads(rev["content_json"]), refs, revision)
                eid = ident("export")
                db.execute("INSERT INTO exports VALUES(?,?,?,?,?,?)", (eid, aid, revision, user["id"], output, now()))
                self.audit(db, user, "word_export_created", aid)
                return {"id": eid, "status": "ready", "revision": revision}
            return self.once(db, user, "export:" + aid, key, payload, operation)

    def download_export(self, user, eid):
        with self.connection() as db:
            row = db.execute("SELECT * FROM exports WHERE id=? AND user_id=?", (eid, user["id"])).fetchone()
            if not row:
                fail(404, "NOT_FOUND", "导出文件不存在或无权限。")
            artifact, _ = self.exportable(db, user, row["artifact_id"], row["revision"])
            return bytes(row["content"]), artifact["title"]

    def search(self, db, user, question, scope="all"):
        compact = re.sub(r"\s+", "", question.casefold())
        tokens = set(re.findall(r"[a-z0-9]{2,}", compact)) | {compact[i:i + 2] for i in range(max(0, len(compact) - 1))}
        tokens -= {"什么", "哪些", "怎么", "需要", "一下", "如何", "可以", "资料", "请问", "这个"}
        matches = []
        for row in db.execute("SELECT * FROM sources WHERE publication='published'"):
            if not self.current_source(db, row) or not self.readable_source(db, user, row) or (scope != "all" and row["id"] != scope):
                continue
            for index, section in enumerate(json.loads(row["sections_json"]), 1):
                target = (row["title"] + section["title"] + section["text"]).casefold()
                hits = sum(token in target for token in tokens)
                if hits >= 1 and hits / max(1, len(tokens)) >= 0.10:
                    matches.append({"source_id": row["id"], "version_id": row["version_id"], "title": row["title"], "label": row["label"], "locator": source_locator(section, index - 1), "section_index": index - 1, "section_hash": fingerprint(section), "excerpt": section["text"][:700], "hits": hits})
        return sorted(matches, key=lambda item: (-item["hits"], item["source_id"], item["locator"]))[:5]

    def conversation_row(self, db, user, cid):
        self.artifact_user(db, user)
        row = db.execute("SELECT * FROM conversations WHERE id=? AND user_id=?", (cid, user["id"])).fetchone()
        if not row:
            fail(404, "NOT_FOUND", "会话不存在或无访问权限。")
        return row

    @staticmethod
    def conversation_view(db, row):
        last = db.execute("SELECT * FROM conversation_messages WHERE conversation_id=? ORDER BY sequence DESC LIMIT 1", (row["id"],)).fetchone()
        return {**dict(row), "message_count": last["sequence"] if last else 0, "state": "archived" if row["status"] == "archived" else "waiting_for_info" if last and last["reply_type"] == "needs_info" else "ready", "last_message_id": last["message_id"] if last else None}

    def list_conversations(self, user):
        with self.connection() as db:
            db.execute("BEGIN")
            self.artifact_user(db, user)
            return [self.conversation_view(db, row) for row in db.execute("SELECT * FROM conversations WHERE user_id=? ORDER BY updated_at DESC,rowid DESC LIMIT 50", (user["id"],))]

    def get_conversation(self, user, cid):
        with self.connection() as db:
            db.execute("BEGIN")
            return self.conversation_view(db, self.conversation_row(db, user, cid))

    def create_conversation(self, user, payload, key):
        title = text(payload.get("title", "新会话"), "会话名称", 80)
        if set(payload) - {"title"}:
            fail(422, "INVALID_INPUT", "会话创建仅接受名称，归属由当前账号确定。")
        with self.connection(write=True) as db:
            def operation():
                if db.execute("SELECT count(*) FROM conversations WHERE user_id=?", (user["id"],)).fetchone()[0] >= 50:
                    fail(409, "CONVERSATION_LIMIT", "当前演示空间每人最多50个会话，归档不回收额度。")
                cid, stamp = ident("conv"), now()
                db.execute("INSERT INTO conversations(id,user_id,title,created_at,updated_at) VALUES(?,?,?,?,?)", (cid, user["id"], title, stamp, stamp))
                self.audit(db, user, "conversation_created", cid)
                return {"id": cid}
            result = self.once(db, user, "conversation_create", key, payload, operation)
            return self.conversation_view(db, self.conversation_row(db, user, result["id"]))

    def update_conversation(self, user, cid, payload, key):
        base = version(payload.get("base_version"))
        if set(payload) - {"base_version", "title", "status"} or not ({"title", "status"} & set(payload)):
            fail(422, "INVALID_INPUT", "请修改会话名称或归档状态。")
        if "status" in payload and payload["status"] not in ("active", "archived"):
            fail(422, "INVALID_INPUT", "会话状态无效。")
        title = text(payload["title"], "会话名称", 80) if "title" in payload else None
        with self.connection(write=True) as db:
            self.conversation_row(db, user, cid)
            def operation():
                row = self.conversation_row(db, user, cid)
                if row["version"] != base:
                    fail(409, "CONVERSATION_CONFLICT", "会话已有更新，请重新读取后确认。")
                db.execute("UPDATE conversations SET title=?,status=?,version=version+1,updated_at=? WHERE id=?", (title if title is not None else row["title"], payload.get("status", row["status"]), now(), cid))
                self.audit(db, user, "conversation_updated", cid)
                return {"version": base + 1}
            result = self.once(db, user, "conversation_update:" + cid, key, payload, operation)
            current = self.conversation_view(db, self.conversation_row(db, user, cid))
            return {**current, "change_result": {"applied_version": result["version"], "is_current": current["version"] == result["version"]}}

    def conversation_messages(self, user, cid, cursor=None):
        with self.connection() as db:
            db.execute("BEGIN")
            row = self.conversation_row(db, user, cid)
            info = self.conversation_view(db, row)
            if cursor is None:
                before = info["message_count"] + 1
            elif (type(cursor) is int or isinstance(cursor, str)) and re.fullmatch(r"[1-9][0-9]{0,5}", str(cursor)) and int(cursor) <= info["message_count"] + 1:
                before = int(cursor)
            else:
                fail(422, "INVALID_CURSOR", "消息分页位置无效，请返回最新消息。")
            rows = db.execute("SELECT m.* FROM messages m JOIN conversation_messages c ON c.message_id=m.id WHERE c.conversation_id=? AND c.sequence<? ORDER BY c.sequence DESC LIMIT 20", (cid, before)).fetchall()
            messages = [self.message_view(db, user, item) for item in reversed(rows)]
            next_cursor = messages[0]["sequence"] if messages and messages[0]["sequence"] > 1 else None
            return {"conversation": info, "messages": messages, "next_cursor": next_cursor}

    def ask_conversation(self, user, cid, payload, key):
        question, base = text(payload.get("text"), "问题", 500), version(payload.get("base_version"))
        scope, reply_to = payload.get("scope", "all"), payload.get("reply_to")
        if not isinstance(scope, str) or not scope or len(scope) > 80 or reply_to is not None and (not isinstance(reply_to, str) or not re.fullmatch(r"msg_[a-f0-9]{32}", reply_to)):
            fail(422, "INVALID_INPUT", "资料范围或补充信息目标无效。")
        if set(payload) - {"text", "scope", "base_version", "reply_to"}:
            fail(422, "INVALID_INPUT", "当前会话不支持该上下文参数。")
        with self.connection(write=True) as db:
            self.conversation_row(db, user, cid)
            def operation():
                row = self.conversation_row(db, user, cid)
                if row["version"] != base:
                    fail(409, "CONVERSATION_CONFLICT", "会话已在其他页面更新，请刷新后核对再发送。")
                if row["status"] != "active":
                    fail(409, "CONVERSATION_ARCHIVED", "会话已归档，请先明确恢复或另建会话。")
                info = self.conversation_view(db, row)
                if info["message_count"] >= 100:
                    fail(409, "MESSAGE_LIMIT", "每个演示会话最多100条问答，请另建会话。")
                if scope != "all":
                    self.source(db, user, scope, True, current=True)
                query = question
                if reply_to:
                    parent = db.execute("SELECT m.question,c.reply_type FROM messages m JOIN conversation_messages c ON c.message_id=m.id WHERE c.conversation_id=? AND m.id=?", (cid, reply_to)).fetchone()
                    if not parent or info["last_message_id"] != reply_to or parent["reply_type"] != "needs_info":
                        fail(409, "CLARIFICATION_CHANGED", "追问目标已变化，请读取当前会话；未合并其他会话内容。")
                    query = parent["question"] + " " + question
                vague = re.sub(r"[\s?？!！。,.，]", "", question) in {"你好", "请问", "怎么办", "怎么做", "怎么操作", "如何操作", "这个", "那个", "这个怎么做", "这是什么", "帮我看看", "帮我查一下", "怎么弄"}
                kind = "needs_info" if vague else None
                matches = [] if vague else self.search(db, user, query, scope)
                kind = kind or ("evidence_excerpt" if matches else "no_evidence")
                prompt = "请补充具体事项、目标或资料名称，再继续查询。" if vague else ""
                mid, stamp = ident("msg"), now()
                refs = [{field: item[field] for field in ("source_id", "version_id", "locator", "section_index", "section_hash")} for item in matches]
                db.execute("INSERT INTO messages VALUES(?,?,?,?,?)", (mid, user["id"], question, dump(refs), stamp))
                db.execute("INSERT INTO conversation_messages VALUES(?,?,?,?,?,?,?)", (mid, cid, info["message_count"] + 1, kind, reply_to, prompt, scope))
                title = question[:80] if info["message_count"] == 0 and row["title"] == "新会话" else row["title"]
                db.execute("UPDATE conversations SET title=?,version=version+1,updated_at=? WHERE id=?", (title, stamp, cid))
                self.audit(db, user, "conversation_queried", mid)
                return {"id": mid}
            result = self.once(db, user, "conversation_ask:" + cid, key, payload, operation)
            return {"conversation": self.conversation_view(db, self.conversation_row(db, user, cid)), "message": self.message_view(db, user, self.message_row(db, user, result["id"]))}

    def ask(self, user, payload, key):
        question = text(payload.get("question"), "问题", 500)
        scope = payload.get("scope", "all")
        if not isinstance(scope, str):
            fail(422, "INVALID_SCOPE", "资料范围无效。")
        with self.connection(write=True) as db:
            def operation():
                matches = self.search(db, user, question, scope)
                mid = ident("msg")
                db.execute("INSERT INTO messages VALUES(?,?,?,?,?)", (mid, user["id"], question, dump([{field: item[field] for field in ("source_id", "version_id", "locator", "section_index", "section_hash")} for item in matches]), now()))
                self.audit(db, user, "knowledge_queried", mid)
                return {"id": mid}
            result = self.once(db, user, "assistant_query", key, payload, operation)
        return self.messages(user, result["id"])[0]

    def message_row(self, db, user, mid):
        row = db.execute("SELECT * FROM messages WHERE id=? AND user_id=?", (mid, user["id"])).fetchone()
        if not row:
            fail(404, "NOT_FOUND", "记录不存在、已失效或无访问权限。")
        return row

    def resolve_evidence(self, db, user, mid, slot, ref):
        # Stored slot and fingerprint survive filtering; never retarget legacy locators.
        index = ref.get("section_index")
        if type(index) is not int or index < 0 or not isinstance(ref.get("section_hash"), str):
            return None
        src = db.execute("SELECT * FROM sources WHERE id=?", (ref.get("source_id"),)).fetchone()
        if not self.readable_source(db, user, src) or src["publication"] != "published" or src["version_id"] != ref.get("version_id"):
            return None
        sections = json.loads(src["sections_json"])
        if index >= len(sections) or fingerprint(sections[index]) != ref["section_hash"]:
            return None
        section = sections[index]
        locator = source_locator(section, index)
        if ref.get("locator") != locator:
            return None
        return {"evidence_id": "ev_" + fingerprint([mid, slot, ref]), "source_id": src["id"], "version_id": src["version_id"], "title": src["title"], "label": src["label"], "category": self.source_category(db, src)["name"], "locator": locator, "section_index": index, "section": section, "historical": not self.current_source(db, src)}

    def get_evidence(self, user, mid, eid):
        with self.connection() as db:
            row = self.message_row(db, user, mid)
            for slot, ref in enumerate(json.loads(row["refs_json"])):
                if eid == "ev_" + fingerprint([mid, slot, ref]):
                    evidence = self.resolve_evidence(db, user, mid, slot, ref)
                    if evidence:
                        return {"message_id": mid, **evidence}
            fail(404, "NOT_FOUND", "引用不存在、已失效或无访问权限。请重新查询。")

    def message_view(self, db, user, row):
        refs, matches = json.loads(row["refs_json"]), []
        for slot, ref in enumerate(refs):
            evidence = self.resolve_evidence(db, user, row["id"], slot, ref)
            if evidence:
                section = evidence.pop("section")
                matches.append({**evidence, "excerpt": section["text"][:700]})
        feedback = db.execute("SELECT * FROM feedback WHERE message_id=? AND user_id=?", (row["id"], user["id"])).fetchone()
        result = {"id": row["id"], "question": row["question"], "created_at": row["created_at"], "matches": matches, "unavailable_count": len(refs) - len(matches), "answer_type": "cited_excerpts" if matches else "no_evidence", "mode": "offline_keyword", "feedback": self.feedback_view(feedback, own=True) if feedback else None}
        conversation = db.execute("SELECT * FROM conversation_messages WHERE message_id=?", (row["id"],)).fetchone()
        if conversation:
            kind = "needs_info" if conversation["reply_type"] == "needs_info" else "evidence_excerpt" if matches else "no_evidence"
            result.update(conversation_id=conversation["conversation_id"], sequence=conversation["sequence"], reply_type=kind, clarification_for=conversation["clarification_for"], clarification_prompt=conversation["clarification_prompt"], scope=conversation["scope"])
            if kind == "needs_info": result["answer_type"] = kind
        return result

    def messages(self, user, message_id=None):
        with self.connection() as db:
            result = [self.message_view(db, user, row) for row in db.execute("SELECT * FROM messages WHERE user_id=? AND (? IS NULL OR id=?) AND NOT EXISTS(SELECT 1 FROM conversation_messages c WHERE c.message_id=messages.id) ORDER BY created_at DESC,rowid DESC LIMIT 20", (user["id"], message_id, message_id))]
            return list(reversed(result))

    @staticmethod
    def feedback_view(row, own=False):
        fields = ("id", "rating", "reason", "answer_type", "status", "resolution", "version", "created_at", "updated_at")
        return {field: row[field] for field in fields + (("note",) if own else ())}

    def submit_feedback(self, user, mid, payload, key):
        rating, reason = payload.get("rating"), payload.get("reason")
        if rating not in ("helpful", "unhelpful") or reason not in ("useful", "irrelevant", "missing", "outdated", "locator", "other") or (rating == "helpful") != (reason == "useful"):
            fail(422, "INVALID_INPUT", "请选择有效的评价和问题原因。")
        note = text(payload.get("note", ""), "个人备注", 500, False)
        with self.connection(write=True) as db:
            def operation():
                message = self.message_row(db, user, mid)
                if db.execute("SELECT 1 FROM feedback WHERE message_id=?", (mid,)).fetchone():
                    fail(409, "FEEDBACK_EXISTS", "此条回答已反馈，请刷新查看处理状态。")
                view = self.message_view(db, user, message)
                if view["answer_type"] == "needs_info":
                    fail(409, "FEEDBACK_NOT_APPLICABLE", "请先补充信息；追问提示不作为知识回答评价。")
                fid, stamp = ident("feedback"), now()
                db.execute("INSERT INTO feedback(id,message_id,user_id,rating,reason,note,answer_type,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)", (fid, mid, user["id"], rating, reason, note, view["answer_type"], stamp, stamp))
                self.audit(db, user, "answer_feedback_submitted", fid)
                return {"id": fid, "answer_type": view["answer_type"], "status": "pending", "version": 1}
            return self.once(db, user, "feedback:" + mid, key, payload, operation)

    def admin_feedback(self, user):
        if user["role"] != "admin":
            fail(403, "ADMIN_REQUIRED", "需要管理员权限。")
        with self.connection() as db:
            self.require_admin(db, user)
            return [self.feedback_view(row) for row in db.execute("SELECT * FROM feedback ORDER BY status='closed',updated_at DESC,rowid DESC LIMIT 100")]

    def update_feedback(self, user, fid, payload, key):
        if user["role"] != "admin":
            fail(403, "ADMIN_REQUIRED", "需要管理员权限。")
        target = payload.get("status")
        if target not in ("in_progress", "closed"):
            fail(422, "INVALID_INPUT", "请选择处理中或已关闭。")
        resolution = text(payload.get("resolution", ""), "处理备注", 500, target == "closed")
        base = version(payload.get("base_version"))
        with self.connection(write=True) as db:
            self.require_admin(db, user)
            def operation():
                row = db.execute("SELECT * FROM feedback WHERE id=?", (fid,)).fetchone()
                if not row:
                    fail(404, "NOT_FOUND", "反馈记录不存在。")
                if row["version"] != base or row["status"] == "closed" or row["status"] == target:
                    fail(409, "VERSION_CONFLICT", "反馈状态已变化，请重新读取。")
                db.execute("UPDATE feedback SET status=?,resolution=?,version=version+1,updated_at=? WHERE id=?", (target, resolution, now(), fid))
                self.audit(db, user, "feedback_" + target, fid)
                return {"id": fid, "status": target, "version": base + 1}
            return self.once(db, user, "feedback_status:" + fid, key, payload, operation)

    def task_preview(self, user, payload, key):
        title = text(payload.get("title"), "任务标题", 120)
        note = text(payload.get("note", ""), "任务说明", 2000, False)
        due = text(payload.get("due_date", ""), "日期", 10, False)
        if due:
            try:
                date.fromisoformat(due)
            except ValueError:
                fail(422, "INVALID_DATE", "请填写有效日期。")
        with self.connection(write=True) as db:
            def operation():
                assignee_id = text(payload.get("assignee_id"), "负责人标识", 80)
                assignee = db.execute("SELECT id,name FROM users WHERE id=? AND enabled=1", (assignee_id,)).fetchone()
                if not assignee:
                    fail(422, "ASSIGNEE_REQUIRED", "请选择任务负责人。")
                preview = {"title": title, "note": note, "due_date": due, "assignee_id": assignee[0], "assignee_name": assignee[1]}
                pid = ident("preview")
                db.execute("INSERT INTO task_previews(id,creator_id,payload_json,expires) VALUES(?,?,?,?)", (pid, user["id"], dump(preview), time.time() + 900))
                return {"id": pid, "status": "awaiting_confirmation", **preview}
            return self.once(db, user, "task_preview", key, payload, operation)

    def confirm_task(self, user, pid, payload, key):
        with self.connection(write=True) as db:
            def operation():
                preview = db.execute("SELECT * FROM task_previews WHERE id=? AND creator_id=?", (pid, user["id"])).fetchone()
                if not preview:
                    fail(404, "NOT_FOUND", "预览不存在或无权限。")
                if preview["task_id"]:
                    task = db.execute("SELECT status FROM tasks WHERE id=?", (preview["task_id"],)).fetchone()
                    return {"id": preview["task_id"], "status": task["status"]}
                if preview["expires"] < time.time():
                    fail(409, "PREVIEW_EXPIRED", "任务预览已过期，请重新预览。")
                data = json.loads(preview["payload_json"])
                if not db.execute("SELECT 1 FROM users WHERE id=? AND enabled=1", (data["assignee_id"],)).fetchone():
                    fail(422, "ASSIGNEE_REQUIRED", "负责人已停用，请重新选择并预览任务。")
                tid, stamp = ident("task"), now()
                db.execute("INSERT INTO tasks(id,creator_id,assignee_id,title,note,due_date,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)", (tid, user["id"], data["assignee_id"], data["title"], data["note"], data["due_date"], stamp, stamp))
                db.execute("UPDATE task_previews SET task_id=? WHERE id=?", (tid, pid))
                self.audit(db, user, "task_confirmed", tid)
                return {"id": tid, "status": "pending"}
            return self.once(db, user, "task_confirm:" + pid, key, payload, operation)

    def tasks(self, user):
        with self.connection() as db:
            return [dict(row) for row in db.execute("SELECT t.*,u.name AS assignee_name FROM tasks t JOIN users u ON u.id=t.assignee_id WHERE creator_id=? OR assignee_id=? ORDER BY t.updated_at DESC LIMIT 100", (user["id"], user["id"]))]

    def update_task(self, user, tid, payload, key):
        target = payload.get("status")
        if target not in {"in_progress", "completed", "canceled"}:
            fail(422, "INVALID_INPUT", "任务状态无效。")
        with self.connection(write=True) as db:
            def operation():
                row = db.execute("SELECT * FROM tasks WHERE id=? AND (creator_id=? OR assignee_id=?)", (tid, user["id"], user["id"])).fetchone()
                if not row:
                    fail(404, "NOT_FOUND", "任务不存在或无权限。")
                if row["version"] != version(payload.get("base_version")) or row["status"] in {"completed", "canceled"}:
                    fail(409, "VERSION_CONFLICT", "任务状态已变化，请刷新。")
                if (target == "canceled" and row["creator_id"] != user["id"]) or (target != "canceled" and row["assignee_id"] != user["id"]):
                    fail(403, "TASK_ROLE_REQUIRED", "只有负责人可以处理，只有创建人可以取消。")
                db.execute("UPDATE tasks SET status=?,version=version+1,updated_at=? WHERE id=?", (target, now(), tid))
                self.audit(db, user, "task_" + target, tid)
                return {"id": tid, "status": target, "version": row["version"] + 1}
            return self.once(db, user, "task_update:" + tid, key, payload, operation)

    def admin_summary(self, user):
        if user["role"] != "admin":
            fail(403, "ADMIN_REQUIRED", "需要管理员权限。")
        with self.connection() as db:
            self.require_admin(db, user)
            return {"mode": "offline_keyword", "model_connected": False, "users": self.users(), "audit": [dict(row) for row in db.execute("SELECT a.action,a.created_at,u.name AS actor_name FROM audit a LEFT JOIN users u ON u.id=a.actor_id ORDER BY a.id DESC LIMIT 40")]}
