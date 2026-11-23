import json
import re
import time
from datetime import date

from store import Problem, fail, fingerprint, ident, now, source_locator, text, version


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

    def create_project(self, db, user, body, key):
        name = text(body.get("name"), "项目名称", 100)
        due = text(body.get("due_date"), "截止日期", 10)
        try:
            if date.fromisoformat(due).isoformat() != due:
                raise ValueError()
        except ValueError:
            fail(422, "INVALID_INPUT", "截止日期须为 YYYY-MM-DD。")
        reviewer = text(body.get("reviewer_id"), "审核人", 80)
        source_id = text(body.get("source_id"), "验收依据", 80)
        items = body.get("items")
        if not isinstance(items, list) or not 1 <= len(items) <= 20:
            fail(422, "INVALID_INPUT", "材料清单须包含 1 至 20 项。")
        names = [text(item, "材料名称", 100) for item in items]
        if len(set(names)) != len(names):
            fail(422, "INVALID_INPUT", "材料名称不能重复。")

        def operation():
            if not db.execute("SELECT 1 FROM users WHERE id=? AND enabled=1", (reviewer,)).fetchone():
                fail(422, "ASSIGNEE_REQUIRED", "审核人不存在或已停用。")
            self.store.source(db, user, source_id, published=True, current=True)
            self.store.source(db, {"id": reviewer}, source_id, published=True, current=True)
            pid = ident("project")
            db.execute("INSERT INTO acceptance_projects(id,name,due_date,owner_id,reviewer_id,source_id) VALUES(?,?,?,?,?,?)",
                       (pid, name, due, user["id"], reviewer, source_id))
            for item_name in names:
                db.execute("INSERT INTO acceptance_items(project_id,item_id,name,assignee_id,updated_at) VALUES(?,?,?,?,?)",
                           (pid, ident("item"), item_name, reviewer, now()))
            self.store.audit(db, user, "acceptance_project_created", pid)
            return {"id": pid, "version": 1}
        return self.store.once(db, user, "acceptance_project_create", key, body, operation)

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

    def evidence(self, db, user, project, *, snapshot=False):
        try:
            source = self.store.source(db, user, project["source_id"], published=True, current=True)
        except Problem as error:
            if error.status != 404:
                raise
            return None
        sections = json.loads(source["sections_json"])
        if not sections:
            return None
        if snapshot:
            return fingerprint({"id": source["id"], "version_id": source["version_id"],
                                "version": source["version"], "label": source["label"],
                                "title": source["title"], "sections": sections})
        return {"source_id": source["id"], "title": source["title"], "version": source["label"],
                "locator": source_locator(sections[0], 0), "text": sections[0]["text"]}

    def require_check(self, db, user, project, base):
        if project["version"] != base:
            fail(409, "ACCEPTANCE_CHANGED", "台账已更新，请重新检查。")
        if self.evidence(db, user, project) is None:
            fail(409, "ACCEPTANCE_EVIDENCE_UNAVAILABLE", "验收依据不可用，暂不能核对缺项。")

    def review_snapshot(self, db, project, *, with_manifest=False):
        people = ({"id": project["owner_id"]}, {"id": project["reviewer_id"]})
        evidence = [self.evidence(db, person, project, snapshot=True) for person in people]
        if not all(evidence) or evidence[0] != evidence[1]:
            fail(409, "ACCEPTANCE_EVIDENCE_UNAVAILABLE", "双方须能访问当前验收依据。")
        items = [dict(row) for row in db.execute(
            "SELECT item_id,name,material_version,material_source_id,material_source_version_id FROM acceptance_items WHERE project_id=? ORDER BY item_id", (project["id"],))]
        if not items or any(not item["material_version"] for item in items):
            fail(409, "ACCEPTANCE_MATERIALS_MISSING", "请先登记全部材料。")
        for item in items:
            if item["material_source_id"]:
                try:
                    sources = [self.store.source(db, person, item["material_source_id"], published=True, current=True) for person in people]
                except Problem as error:
                    if error.status != 404:
                        raise
                    fail(409, "ACCEPTANCE_MATERIAL_UNAVAILABLE", "关联资料不可用，请重新核实。")
                if any(source["version_id"] != item["material_source_version_id"] for source in sources):
                    fail(409, "ACCEPTANCE_MATERIAL_UNAVAILABLE", "关联资料版本已变化，请重新核实。")
                item["source_fingerprint"] = fingerprint(dict(sources[0]))
        result = fingerprint({"project_version": project["version"], "evidence": evidence[0], "items": items})
        if with_manifest:
            manifest = [{"name": item["name"], "version": item["material_version"], "linked": bool(item["material_source_id"])} for item in items]
            return result, manifest
        return result

    @staticmethod
    def review_history(db, project):
        rows = db.execute("SELECT r.id,r.status,r.project_version,r.submitted_at,r.decided_at,r.comment,r.manifest_json,"
                          "submitter.name AS submitted_by,decider.name AS decided_by FROM acceptance_reviews r "
                          "JOIN users submitter ON submitter.id=r.submitted_by LEFT JOIN users decider ON decider.id=r.decided_by "
                          "WHERE r.project_id=? ORDER BY r.rowid DESC LIMIT 50", (project["id"],))
        result = []
        for row in rows:
            entry = dict(row)
            raw = entry.pop("manifest_json")
            entry["materials"] = json.loads(raw) if raw else None
            result.append(entry)
        return result

    def review_status(self, db, user, project):
        row = db.execute("SELECT * FROM acceptance_reviews WHERE project_id=? ORDER BY rowid DESC LIMIT 1", (project["id"],)).fetchone()
        if not row:
            return None
        result = {k: row[k] for k in ("id", "status", "submitted_at", "decided_at", "comment")}
        try:
            result["stale"] = row["snapshot_hash"] != self.review_snapshot(db, project)
        except Problem:
            result["stale"] = True
        result["can_decide"] = user["id"] == project["reviewer_id"] and user["id"] != project["owner_id"] and row["status"] == "pending" and not result["stale"]
        return result

    def review_report(self, db, user, project):
        status = self.review_status(db, user, project)
        if not status or status["status"] != "approved" or status["stale"]:
            fail(409, "ACCEPTANCE_REPORT_UNAVAILABLE", "仅当前有效的通过记录可生成审核记录。")
        entry = self.review_history(db, project)[0]
        if entry["materials"] is None:
            fail(409, "ACCEPTANCE_REPORT_UNAVAILABLE", "旧记录未保存材料快照，请重新提交审核。")
        _, current_manifest = self.review_snapshot(db, project, with_manifest=True)
        if entry["materials"] != current_manifest:
            fail(409, "ACCEPTANCE_REPORT_UNAVAILABLE", "材料快照不一致，请重新核实。")
        evidence = self.evidence(db, user, project)
        return {"project": project["name"], "project_version": entry["project_version"], "review_id": entry["id"],
                "submitted_at": entry["submitted_at"], "submitted_by": entry["submitted_by"],
                "decided_at": entry["decided_at"], "decided_by": entry["decided_by"], "comment": entry["comment"],
                "evidence": {"title": evidence["title"], "version": evidence["version"]},
                "materials": entry["materials"], "generated_at": now()}

    def submit_review(self, db, user, project, body, key):
        if user["id"] != project["owner_id"]:
            fail(403, "ACCEPTANCE_OWNER_REQUIRED", "只有项目负责人可以提交验收。")
        def operation():
            if project["owner_id"] == project["reviewer_id"]:
                fail(409, "ACCEPTANCE_REVIEWER_REQUIRED", "验收审核人须与项目负责人不同。")
            if not db.execute("SELECT 1 FROM users WHERE id=? AND enabled=1", (project["reviewer_id"],)).fetchone():
                fail(409, "ACCEPTANCE_REVIEWER_REQUIRED", "审核人已停用。")
            if project["version"] != version(body.get("base_version")):
                fail(409, "ACCEPTANCE_CHANGED", "台账已更新，请重新核对。")
            snapshot, manifest = self.review_snapshot(db, project, with_manifest=True)
            last = db.execute("SELECT status,snapshot_hash FROM acceptance_reviews WHERE project_id=? ORDER BY rowid DESC LIMIT 1", (project["id"],)).fetchone()
            if last and last["snapshot_hash"] == snapshot and last["status"] in {"pending", "approved"}:
                fail(409, "ACCEPTANCE_REVIEW_EXISTS", "当前版本已提交或已通过审核。")
            rid = ident("review")
            db.execute("INSERT INTO acceptance_reviews(id,project_id,project_version,snapshot_hash,submitted_by,submitted_at,status,manifest_json) VALUES(?,?,?,?,?,?,'pending',?)", (rid, project["id"], project["version"], snapshot, user["id"], now(), json.dumps(manifest, ensure_ascii=False)))
            self.store.audit(db, user, "acceptance_review_submitted", rid)
            return {"id": rid, "status": "pending"}
        return self.store.once(db, user, "acceptance_review_submit:" + project["id"], key, body, operation)

    def decide_review(self, db, user, project, body, key):
        if user["id"] != project["reviewer_id"] or user["id"] == project["owner_id"]:
            fail(403, "ACCEPTANCE_REVIEWER_REQUIRED", "只有项目审核人可以作出验收决定。")
        rid = text(body.get("review_id"), "审核编号", 100)
        decision = body.get("decision")
        if decision not in ("approved", "rejected"):
            fail(422, "INVALID_INPUT", "请选择通过或退回。")
        comment = text(body.get("comment", ""), "审核意见", 500, required=decision == "rejected")
        def operation():
            row = db.execute("SELECT * FROM acceptance_reviews WHERE project_id=? ORDER BY rowid DESC LIMIT 1", (project["id"],)).fetchone()
            if not row or row["id"] != rid or row["status"] != "pending":
                fail(409, "ACCEPTANCE_REVIEW_CHANGED", "审核状态已变化，请刷新。")
            if row["snapshot_hash"] != self.review_snapshot(db, project):
                fail(409, "ACCEPTANCE_REVIEW_STALE", "台账或依据已变化，请重新提交。")
            db.execute("UPDATE acceptance_reviews SET status=?,decided_by=?,decided_at=?,comment=? WHERE id=?", (decision, user["id"], now(), comment, rid))
            self.store.audit(db, user, "acceptance_review_" + decision, rid)
            return {"id": rid, "status": decision}
        return self.store.once(db, user, "acceptance_review_decide:" + project["id"], key, body, operation)

    def detail(self, db, user, project):
        evidence = self.evidence(db, user, project)
        items = [dict(row) for row in db.execute(
            "SELECT i.item_id,i.name,i.material_version,i.material_source_id,i.material_source_version_id,i.updated_at,u.name AS assignee_name,t.id AS task_id,t.status AS task_status "
            "FROM acceptance_items i JOIN users u ON i.assignee_id=u.id LEFT JOIN tasks t ON i.task_id=t.id "
            "WHERE i.project_id=? ORDER BY i.rowid", (project["id"],))]
        for item in items:
            sid, vid = item.pop("material_source_id"), item.pop("material_source_version_id")
            item["material_reference"] = None
            if sid:
                try:
                    source = self.store.source(db, user, sid, published=True, current=True)
                    if source["version_id"] != vid:
                        raise ValueError("version changed")
                    item["material_reference"] = {"state": "available", "source_id": sid, "title": source["title"], "version": source["label"]}
                except (Problem, ValueError):
                    item["material_reference"] = {"state": "unavailable"}
        summary = {"missing": sum(item["material_version"] is None for item in items), "total": len(items)} if evidence else None
        can_update = user["id"] == project["owner_id"]
        candidates = [dict(row) for row in db.execute("SELECT id,name FROM users WHERE enabled=1 AND id IN (?,?) ORDER BY name", (project["owner_id"], project["reviewer_id"]))] if can_update else []
        return {**{k: project[k] for k in ("id", "name", "due_date", "version")},
                "evidence": evidence, "items": items, "summary": summary, "can_update": can_update, "assignee_candidates": candidates, "review": self.review_status(db, user, project), "review_history": self.review_history(db, project)}

    def reassign(self, db, user, project, item, body, key):
        if user["id"] != project["owner_id"]:
            fail(403, "ACCEPTANCE_OWNER_REQUIRED", "只有项目负责人可以修改材料负责人。")
        assignee = body.get("assignee_id")
        def operation():
            if project["version"] != version(body.get("base_version")):
                fail(409, "ACCEPTANCE_CHANGED", "台账已更新，请重新核对。")
            if item["material_version"]:
                fail(409, "ACCEPTANCE_ITEM_RECEIVED", "材料已到件，不能改派。")
            task = db.execute("SELECT status FROM tasks WHERE id=?", (item["task_id"],)).fetchone() if item["task_id"] else None
            if item["task_id"] and (task is None or task["status"] not in {"canceled", "completed"}):
                fail(409, "ACCEPTANCE_TASK_ACTIVE", "已有待办，请先处理或取消待办。")
            if assignee not in (project["owner_id"], project["reviewer_id"]) or not db.execute("SELECT 1 FROM users WHERE id=? AND enabled=1", (assignee,)).fetchone():
                fail(422, "ASSIGNEE_REQUIRED", "请选择启用的项目成员。")
            db.execute("UPDATE acceptance_items SET assignee_id=?,updated_at=? WHERE project_id=? AND item_id=?", (assignee, now(), project["id"], item["item_id"]))
            db.execute("UPDATE acceptance_projects SET version=version+1 WHERE id=?", (project["id"],))
            self.store.audit(db, user, "acceptance_assignee_changed", project["id"] + ":" + item["item_id"])
            return {"version": project["version"] + 1}
        return self.store.once(db, user, "acceptance_reassign:" + project["id"] + ":" + item["item_id"], key, body, operation)

    def preview(self, db, user, project, item, body, key):
        if user["id"] != project["owner_id"]:
            fail(403, "ACCEPTANCE_OWNER_REQUIRED", "只有项目负责人可以指派补件待办。")
        self.require_check(db, user, project, version(body.get("base_version")))
        task = db.execute("SELECT status FROM tasks WHERE id=?", (item["task_id"],)).fetchone() if item["task_id"] else None
        if item["material_version"] or (item["task_id"] and (task is None or task["status"] not in {"canceled", "completed"})):
            fail(409, "ACCEPTANCE_ITEM_NOT_PENDING", "材料已到件或已有补件待办。")

        def operation():
            pid = ident("acceptance")
            db.execute("INSERT INTO acceptance_previews(id,project_id,item_id,creator_id,project_version,expires,evidence_fingerprint,previous_task_id) VALUES(?,?,?,?,?,?,?,?)",
                       (pid, project["id"], item["item_id"], user["id"], project["version"], time.time() + 900,
                        self.evidence(db, user, project, snapshot=True), item["task_id"]))
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
            if preview["evidence_fingerprint"] != self.evidence(db, user, project, snapshot=True):
                fail(409, "ACCEPTANCE_EVIDENCE_CHANGED", "验收依据已变化，请重新预览。")
            item = self.item(db, project["id"], preview["item_id"])
            if item["material_version"]:
                fail(409, "ACCEPTANCE_CHANGED", "材料已补齐，请重新检查。")
            task = db.execute("SELECT status FROM tasks WHERE id=?", (item["task_id"],)).fetchone() if item["task_id"] else None
            if item["task_id"] and task is None:
                fail(409, "ACCEPTANCE_TASK_UNAVAILABLE", "原待办不可用，请联系管理员。")
            if task and task["status"] in {"canceled", "completed"} and item["task_id"] != preview["previous_task_id"]:
                fail(409, "ACCEPTANCE_CHANGED", "待办已结束，请重新预览。")
            tid = item["task_id"] if task and task["status"] not in {"canceled", "completed"} else self.store.insert_task(
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
        source_id = body.get("material_source_id") or None
        if source_id is not None:
            source_id = text(source_id, "资料编号", 80)

        def operation():
            if project["version"] != version(body.get("base_version")):
                fail(409, "ACCEPTANCE_CHANGED", "台账已更新，请重新检查。")
            if item["material_version"]:
                fail(409, "ACCEPTANCE_ITEM_RECEIVED", "材料已登记到件。")
            source_version = None
            if source_id:
                source = self.store.source(db, user, source_id, published=True, current=True)
                self.store.source(db, {"id": project["reviewer_id"]}, source_id, published=True, current=True)
                source_version = source["version_id"]
            db.execute("UPDATE acceptance_items SET material_version=?,material_source_id=?,material_source_version_id=?,updated_at=? WHERE project_id=? AND item_id=?",
                       (revision, source_id, source_version, now(), project["id"], item["item_id"]))
            db.execute("UPDATE acceptance_projects SET version=version+1 WHERE id=?", (project["id"],))
            self.store.audit(db, user, "acceptance_material_received", project["id"] + ":" + item["item_id"])
            return {"version": project["version"] + 1}

        return self.store.once(db, user, "acceptance_received:" + project["id"] + ":" + item["item_id"], key, body, operation)

    def relink(self, db, user, project, item, body, key):
        if user["id"] != project["owner_id"]:
            fail(403, "ACCEPTANCE_OWNER_REQUIRED", "只有项目负责人可以更新关联资料。")
        source_id = text(body.get("material_source_id"), "资料编号", 80)
        def operation():
            if project["version"] != version(body.get("base_version")):
                fail(409, "ACCEPTANCE_CHANGED", "台账已更新，请重新核对。")
            if not item["material_version"]:
                fail(409, "ACCEPTANCE_ITEM_NOT_RECEIVED", "请先登记材料到件。")
            source = self.store.source(db, user, source_id, published=True, current=True)
            self.store.source(db, {"id": project["reviewer_id"]}, source_id, published=True, current=True)
            db.execute("UPDATE acceptance_items SET material_source_id=?,material_source_version_id=?,updated_at=? WHERE project_id=? AND item_id=?", (source_id, source["version_id"], now(), project["id"], item["item_id"]))
            db.execute("UPDATE acceptance_projects SET version=version+1 WHERE id=?", (project["id"],))
            self.store.audit(db, user, "acceptance_material_relinked", project["id"] + ":" + item["item_id"])
            return {"version": project["version"] + 1}
        return self.store.once(db, user, "acceptance_relink:" + project["id"] + ":" + item["item_id"], key, body, operation)

    def route(self, method, endpoint, user, body, key):
        with self.store.connection(write=method == "POST") as db:
            if method == "GET":
                db.execute("BEGIN")
                if endpoint == "/acceptance/projects":
                    return [dict(p) for p in db.execute("SELECT id,name,due_date FROM acceptance_projects WHERE ? IN (owner_id,reviewer_id) ORDER BY id", (user["id"],))]
                if match := re.fullmatch(r"/acceptance/projects/([\w-]+)/review/report", endpoint):
                    return self.review_report(db, user, self.project(db, user, match[1]))
                if match := re.fullmatch(r"/acceptance/projects/([\w-]+)", endpoint):
                    return self.detail(db, user, self.project(db, user, match[1]))
            if method == "POST":
                if endpoint == "/acceptance/projects":
                    return self.create_project(db, user, body, key)
                if match := re.fullmatch(r"/acceptance/projects/([\w-]+)/review/(submit|decide)", endpoint):
                    project = self.project(db, user, match[1])
                    return (self.submit_review if match[2] == "submit" else self.decide_review)(db, user, project, body, key)
                if match := re.fullmatch(r"/acceptance/projects/([\w-]+)/items/([\w-]+)/(preview|received|reassign|relink)", endpoint):
                    project = self.project(db, user, match[1])
                    item = self.item(db, project["id"], match[2])
                    action = {"preview": self.preview, "received": self.received, "reassign": self.reassign, "relink": self.relink}[match[3]]
                    return action(db, user, project, item, body, key)
                if match := re.fullmatch(r"/acceptance/previews/([\w-]+)/confirm", endpoint):
                    return self.confirm(db, user, match[1], body, key)
            fail(404, "NOT_FOUND", "接口不存在。")
