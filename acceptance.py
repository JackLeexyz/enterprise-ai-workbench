import json
import re
import time

from store import Problem, fail, ident, now, source_locator, text, version


def seed_demo(db):
    stamp = now()
    db.execute("INSERT INTO acceptance_projects(id,name,due_date,owner_id,reviewer_id,source_id) VALUES(?,?,?,?,?,?)",
               ("demo-a", "星桥园区交付", "2026-12-04", "u_writer", "u_reviewer", "delivery"))
    db.execute("INSERT INTO acceptance_projects(id,name,due_date,owner_id,reviewer_id,source_id) VALUES(?,?,?,?,?,?)",
               ("demo-b", "青屿实施交付", "2026-12-11", "u_admin", "u_admin", "delivery"))
    for pid in ("demo-a", "demo-b"):
        for item, name, revision in (
            ("implementation", "实施说明", "v1.2"),
            ("checklist", "功能验收清单", None),
            ("issues", "问题记录", "v1.0"),
            ("training", "用户培训记录", None),
        ):
            db.execute("INSERT INTO acceptance_items(project_id,item_id,name,assignee_id,material_version,updated_at) VALUES(?,?,?,?,?,?)",
                       (pid, item, name, "u_reviewer" if pid == "demo-a" else "u_admin", revision, stamp))


class AcceptanceDemo:
    def __init__(self, store):
        self.store = store

    @staticmethod
    def project(db, user, pid):
        row = db.execute("SELECT * FROM acceptance_projects WHERE id=? AND ? IN (owner_id,reviewer_id)", (pid, user["id"])).fetchone()
        if row is None:
            fail(404, "NOT_FOUND", "项目不存在或无权访问。")
        return row

    @staticmethod
    def item(db, pid, item_id):
        row = db.execute("SELECT * FROM acceptance_items WHERE project_id=? AND item_id=?", (pid, item_id)).fetchone()
        if row is None:
            fail(404, "NOT_FOUND", "材料项不存在。")
        return row

    def evidence(self, db, user, project):
        try:
            source = self.store.source(db, user, project["source_id"], published=True, current=True)
        except Problem as error:
            if error.status != 404:
                raise
            return None
        sections = json.loads(source["sections_json"])
        if not sections:
            return None
        return {"source_id": source["id"], "title": source["title"], "version": source["label"],
                "locator": source_locator(sections[0], 0), "text": sections[0]["text"]}

    def require_check(self, db, user, project, base):
        if project["version"] != base:
            fail(409, "ACCEPTANCE_CHANGED", "台账已更新，请重新检查。")
        if self.evidence(db, user, project) is None:
            fail(409, "ACCEPTANCE_EVIDENCE_UNAVAILABLE", "验收依据不可用，暂不能核对缺项。")

    def detail(self, db, user, project):
        evidence = self.evidence(db, user, project)
        items = [dict(row) for row in db.execute(
            "SELECT i.item_id,i.name,i.material_version,i.updated_at,u.name AS assignee_name,t.id AS task_id,t.status AS task_status "
            "FROM acceptance_items i JOIN users u ON i.assignee_id=u.id LEFT JOIN tasks t ON i.task_id=t.id "
            "WHERE i.project_id=? ORDER BY i.rowid", (project["id"],))]
        summary = {"missing": sum(item["material_version"] is None for item in items), "total": len(items)} if evidence else None
        return {**{k: project[k] for k in ("id", "name", "due_date", "version")},
                "evidence": evidence, "items": items, "summary": summary, "can_update": user["id"] == project["owner_id"]}

    def preview(self, db, user, project, item, body, key):
        if user["id"] != project["owner_id"]:
            fail(403, "ACCEPTANCE_OWNER_REQUIRED", "只有项目负责人可以指派补件待办。")
        self.require_check(db, user, project, version(body.get("base_version")))
        if item["material_version"] or item["task_id"]:
            fail(409, "ACCEPTANCE_ITEM_NOT_PENDING", "材料已到件或已有补件待办。")

        def operation():
            pid = ident("acceptance")
            db.execute("INSERT INTO acceptance_previews(id,project_id,item_id,creator_id,project_version,expires) VALUES(?,?,?,?,?,?)",
                       (pid, project["id"], item["item_id"], user["id"], project["version"], time.time() + 900))
            assignee = db.execute("SELECT name FROM users WHERE id=? AND enabled=1", (item["assignee_id"],)).fetchone()
            if assignee is None:
                fail(422, "ASSIGNEE_REQUIRED", "材料负责人已停用。")
            return {"id": pid, "title": "补件：" + item["name"], "assignee_name": assignee["name"], "due_date": project["due_date"]}

        return self.store.once(db, user, "acceptance_preview:" + project["id"] + ":" + item["item_id"], key, body, operation)

    def confirm(self, db, user, pid, body, key):
        preview = db.execute("SELECT * FROM acceptance_previews WHERE id=? AND creator_id=?", (pid, user["id"])).fetchone()
        if preview is None:
            fail(404, "NOT_FOUND", "待办预览不存在或无权访问。")
        project = self.project(db, user, preview["project_id"])
        if user["id"] != project["owner_id"]:
            fail(403, "ACCEPTANCE_OWNER_REQUIRED", "只有项目负责人可以确认补件待办。")

        def operation():
            if preview["task_id"]:
                return {"id": preview["task_id"]}
            if preview["expires"] < time.time():
                fail(409, "PREVIEW_EXPIRED", "补件预览已过期。")
            self.require_check(db, user, project, preview["project_version"])
            item = self.item(db, project["id"], preview["item_id"])
            if item["material_version"]:
                fail(409, "ACCEPTANCE_CHANGED", "材料已补齐，请重新检查。")
            tid = item["task_id"] or self.store.insert_task(
                db, user, item["assignee_id"], "补件：" + item["name"],
                "项目验收台账：" + project["name"] + "；依据：交付准备。", project["due_date"])
            db.execute("UPDATE acceptance_items SET task_id=? WHERE project_id=? AND item_id=?", (tid, project["id"], item["item_id"]))
            db.execute("UPDATE acceptance_previews SET task_id=? WHERE id=?", (tid, pid))
            return {"id": tid}

        return self.store.once(db, user, "acceptance_confirm:" + pid, key, body, operation)

    def received(self, db, user, project, item, body, key):
        if user["id"] != project["owner_id"]:
            fail(403, "ACCEPTANCE_OWNER_REQUIRED", "只有项目负责人可以登记到件。")
        revision = text(body.get("material_version"), "材料版本", 30)

        def operation():
            if project["version"] != version(body.get("base_version")):
                fail(409, "ACCEPTANCE_CHANGED", "台账已更新，请重新检查。")
            if item["material_version"]:
                fail(409, "ACCEPTANCE_ITEM_RECEIVED", "材料已登记到件。")
            db.execute("UPDATE acceptance_items SET material_version=?,updated_at=? WHERE project_id=? AND item_id=?",
                       (revision, now(), project["id"], item["item_id"]))
            db.execute("UPDATE acceptance_projects SET version=version+1 WHERE id=?", (project["id"],))
            self.store.audit(db, user, "acceptance_material_received", project["id"] + ":" + item["item_id"])
            return {"version": project["version"] + 1}

        return self.store.once(db, user, "acceptance_received:" + project["id"] + ":" + item["item_id"], key, body, operation)

    def route(self, method, endpoint, user, body, key):
        with self.store.connection(write=method == "POST") as db:
            if method == "GET":
                db.execute("BEGIN")
                if endpoint == "/acceptance/projects":
                    return [dict(p) for p in db.execute("SELECT id,name,due_date FROM acceptance_projects WHERE ? IN (owner_id,reviewer_id) ORDER BY id", (user["id"],))]
                if match := re.fullmatch(r"/acceptance/projects/([\w-]+)", endpoint):
                    return self.detail(db, user, self.project(db, user, match[1]))
            if method == "POST":
                if match := re.fullmatch(r"/acceptance/projects/([\w-]+)/items/([\w-]+)/(preview|received)", endpoint):
                    project = self.project(db, user, match[1])
                    item = self.item(db, project["id"], match[2])
                    action = self.preview if match[3] == "preview" else self.received
                    return action(db, user, project, item, body, key)
                if match := re.fullmatch(r"/acceptance/previews/([\w-]+)/confirm", endpoint):
                    return self.confirm(db, user, match[1], body, key)
            fail(404, "NOT_FOUND", "接口不存在。")
