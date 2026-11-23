"""Concurrent task lifecycle check against an isolated fictional loopback instance."""
import concurrent.futures
import http.cookiejar
import json
import secrets
import sys
import time
import urllib.parse
import urllib.request


def main():
    origin, credentials_file = sys.argv[1:3]
    if urllib.parse.urlsplit(origin).hostname != "127.0.0.1":
        raise SystemExit("Only a loopback instance or SSH tunnel is permitted.")
    with open(credentials_file, encoding="utf-8") as file:
        accounts = {entry["username"]: entry["password"] for entry in json.load(file)["accounts"]}

    def login(name):
        client = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        data = json.dumps({"username": name, "password": accounts[name]}).encode()
        request = urllib.request.Request(origin + "/api/workbench/session", data=data, headers={"Content-Type": "application/json", "Origin": origin})
        with client.open(request, timeout=20) as response:
            return client, json.load(response)["data"]["csrf"]

    def post(session, path, payload):
        client, csrf = session
        request = urllib.request.Request(origin + "/api/workbench" + path, data=json.dumps(payload, ensure_ascii=False).encode(),
            headers={"Content-Type": "application/json", "Origin": origin, "X-CSRF-Token": csrf, "Idempotency-Key": secrets.token_hex(16)})
        with client.open(request, timeout=20) as response:
            return json.load(response)["data"]

    writers = [login("writer") for _ in range(10)]
    reviewer = login("reviewer")
    run_id = secrets.token_hex(6)
    def create(index):
        preview = post(writers[index], "/tasks/preview", {"title": f"虚构并发-{run_id}-{index}", "assignee_id": "u_reviewer", "note": "并发测试", "due_date": ""})
        return post(writers[index], "/task-previews/" + preview["id"] + "/confirm", {})["id"]
    start = time.monotonic()
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
        ids = list(pool.map(create, range(10)))
    if len(set(ids)) != 10:
        raise RuntimeError("task identity collision")
    def complete(task_id):
        return post(reviewer, "/tasks/" + task_id + "/status", {"status": "completed", "base_version": 1, "result_note": "虚构测试已完成"})
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(complete, ids))
    with reviewer[0].open(origin + "/api/workbench/tasks", timeout=20) as response:
        tasks = {entry["id"]: entry for entry in json.load(response)["data"]}
    if any(result["status"] != "completed" or tasks[task_id]["result_note"] != "虚构测试已完成" for task_id, result in zip(ids, results)):
        raise RuntimeError("task readback mismatch")
    print(json.dumps({"concurrent_sessions": 10, "created": len(ids), "unique": len(set(ids)), "completed_and_read_back": len(results),
                      "errors": 0, "wall_seconds": round(time.monotonic() - start, 2)}))


if __name__ == "__main__":
    main()
