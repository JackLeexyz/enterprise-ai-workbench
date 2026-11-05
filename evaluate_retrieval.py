import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import secrets
import tempfile

from store import Store, dump, now

ROOT = Path(__file__).resolve().parent


def evaluate(dataset_path, scratch):
    raw = Path(dataset_path).read_bytes()
    dataset = json.loads(raw)
    fixture_bytes = (ROOT / "fixtures.json").read_bytes()
    with tempfile.TemporaryDirectory(prefix="keyword-eval-", dir=scratch) as temp:
        store = Store(Path(temp) / "evaluation.db")
        store.bootstrap({name: secrets.token_urlsafe(24) for name in ("writer", "reviewer", "admin")}, json.loads(fixture_bytes))
        users = {user["username"]: user for user in store.users()}
        with store.connection(write=True) as db:
            for item in dataset["additional_sources"]:
                db.execute("INSERT INTO sources(id,version_id,title,category,label,owner_id,scope,shares_json,publication,sections_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (item["id"], "ver_" + item["id"], item["title"], "虚构评测", "v1", item["owner_id"], item["scope"], dump(item.get("shared_user_ids", [])), item["publication"], dump(item["sections"]), now(), now()))
        results = []
        for case in dataset["cases"]:
            user, scope = users[case.get("user", "writer")], case.get("scope", "all")
            with store.connection() as db:
                matches = store.search(db, user, case["question"], scope)
                # Evaluate each returned object independently of the search filter.
                violations = []
                for ref in matches:
                    source = db.execute("SELECT * FROM sources WHERE id=?", (ref["source_id"],)).fetchone()
                    shared = user["id"] in json.loads(source["shares_json"])
                    allowed = source["owner_id"] == user["id"] or source["scope"] == "internal" or (source["scope"] == "selected" and shared)
                    if source["publication"] != "published" or not allowed or (scope != "all" and scope != source["id"]):
                        violations.append(ref["source_id"])
            actual = [[ref["source_id"], ref["section_index"]] for ref in matches]
            passed = not actual if case.get("expect_empty") else all(ref in actual for ref in case["required"])
            results.append({"id": case["id"], "group": case["group"], "question": case["question"], "user": case.get("user", "writer"), "scope": scope, "required": case.get("required", []), "expect_empty": case.get("expect_empty", False), "actual_top5": actual, "contract_passed": passed, "security_violations": violations, "note": case.get("note", "")})
    groups = {}
    for item in results:
        group = groups.setdefault(item["group"], {"passed": 0, "total": 0})
        group["total"] += 1
        group["passed"] += int(item["contract_passed"])
    return {"checked_at": datetime.now(timezone.utc).isoformat(), "dataset_version": dataset["dataset_version"], "dataset_sha256": hashlib.sha256(raw).hexdigest(), "fixture_sha256": hashlib.sha256(fixture_bytes).hexdigest(), "algorithm": "current Store.search; lowercase character bigrams and ASCII terms; hit ratio >= 0.10; top 5", "model_calls": 0, "quality_gate": "informational, failures retained", "security_gate_passed": not any(item["security_violations"] for item in results), "contract_passed": sum(item["contract_passed"] for item in results), "case_count": len(results), "groups": groups, "cases": results, "limitations": ["Manually authored synthetic cases, not representative customer distribution", "A retrieved excerpt is not necessarily an answer to the question", "Not an LLM, semantic RAG, answer correctness or load benchmark", "Security regressions fail the command; retrieval quality failures are reported without hiding them", "Original runtime database and customer materials are never read"]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--dataset", default=str(ROOT / "tests/retrieval_cases.json"))
    args = parser.parse_args()
    output = Path(args.out).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    report = evaluate(args.dataset, output.parent)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({key: report[key] for key in ("dataset_version", "case_count", "contract_passed", "groups", "quality_gate", "security_gate_passed", "model_calls")}, ensure_ascii=False))
    if not report["security_gate_passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
