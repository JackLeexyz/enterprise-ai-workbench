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
    ids = [case["id"] for case in dataset["cases"]]
    if len(ids) != len(set(ids)):
        raise ValueError("Evaluation case IDs must be unique")
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
    return {"checked_at": datetime.now(timezone.utc).isoformat(), "dataset_version": dataset["dataset_version"], "dataset_sha256": hashlib.sha256(raw).hexdigest(), "fixture_sha256": hashlib.sha256(fixture_bytes).hexdigest(), "store_sha256": hashlib.sha256((ROOT / "store.py").read_bytes()).hexdigest(), "algorithm": "current Store.search; lowercase character bigrams and ASCII terms; hit ratio >= 0.10; top 5", "model_calls": 0, "quality_gate": "informational, failures retained", "security_gate_passed": not any(item["security_violations"] for item in results), "contract_passed": sum(item["contract_passed"] for item in results), "case_count": len(results), "groups": groups, "cases": results, "limitations": ["Manually authored synthetic cases, not representative customer distribution", "A retrieved excerpt is not necessarily an answer to the question", "Not an LLM, semantic RAG, answer correctness or load benchmark", "Security regressions fail the command; retrieval quality failures are reported without hiding them", "Original runtime database and customer materials are never read"]}


def compare(baseline, current):
    for key in ("dataset_sha256", "fixture_sha256"):
        if not baseline.get(key) or baseline.get(key) != current.get(key):
            raise ValueError(f"Cannot compare reports: {key} differs or is missing")
    before = {case["id"]: case for case in baseline["cases"]}
    after = {case["id"]: case for case in current["cases"]}
    if len(before) != len(baseline["cases"]) or len(after) != len(current["cases"]) or before.keys() != after.keys():
        raise ValueError("Cannot compare reports: case IDs differ or are duplicated")
    return {"baseline_checked_at": baseline.get("checked_at"), "baseline_store_sha256": baseline.get("store_sha256"),
            "regressions": sorted(k for k in before if before[k]["contract_passed"] and not after[k]["contract_passed"]),
            "improvements": sorted(k for k in before if not before[k]["contract_passed"] and after[k]["contract_passed"]),
            "security_regressions": sorted(k for k in before if not before[k]["security_violations"] and after[k]["security_violations"])}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--dataset", default=str(ROOT / "tests/retrieval_cases.json"))
    parser.add_argument("--baseline", help="Prior report from the same dataset and fixtures")
    args = parser.parse_args()
    output = Path(args.out).resolve()
    if args.baseline and output == Path(args.baseline).resolve():
        parser.error("Output must not overwrite the baseline report")
    output.parent.mkdir(parents=True, exist_ok=True)
    report = evaluate(args.dataset, output.parent)
    if args.baseline:
        try:
            report["comparison"] = compare(json.loads(Path(args.baseline).read_text()), report)
        except ValueError as error:
            parser.error(str(error))
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    summary = {key: report[key] for key in ("dataset_version", "case_count", "contract_passed", "groups", "quality_gate", "security_gate_passed", "model_calls")}
    if "comparison" in report:
        summary["comparison"] = report["comparison"]
    print(json.dumps(summary, ensure_ascii=False))
    if not report["security_gate_passed"] or (args.baseline and report["comparison"]["regressions"]):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
