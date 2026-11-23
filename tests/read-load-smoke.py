"""Small read-only concurrency smoke test for an isolated fictional instance."""
import concurrent.futures
import http.cookiejar
import json
import math
import statistics
import sys
import time
import urllib.parse
import urllib.request


def main():
    origin, credentials_file = sys.argv[1:3]
    if urllib.parse.urlsplit(origin).hostname != "127.0.0.1":
        raise SystemExit("Only a loopback instance or SSH tunnel is permitted.")
    with open(credentials_file, encoding="utf-8") as file:
        accounts = json.load(file)["accounts"]
    writer = next(account for account in accounts if account["username"] == "writer")
    clients = []
    for _ in range(10):
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        data = json.dumps({"username": "writer", "password": writer["password"]}).encode()
        request = urllib.request.Request(origin + "/api/workbench/session", data=data, headers={"Content-Type": "application/json", "Origin": origin})
        with opener.open(request, timeout=15) as response:
            if response.status != 200:
                raise RuntimeError("login failed")
        clients.append(opener)

    paths = ("/sources", "/artifacts", "/tasks", "/acceptance/projects", "/acceptance/projects/demo-a")
    def run(client):
        elapsed = []
        for _ in range(3):
            for path in paths:
                start = time.monotonic()
                with client.open(origin + "/api/workbench" + path, timeout=20) as response:
                    body = json.load(response)
                    if response.status != 200 or "data" not in body:
                        raise RuntimeError("unexpected response")
                elapsed.append((time.monotonic() - start) * 1000)
        return elapsed

    for count in (1, 3, 10):
        start = time.monotonic()
        with concurrent.futures.ThreadPoolExecutor(max_workers=count) as pool:
            batches = list(pool.map(run, clients[:count]))
        samples = sorted(value for batch in batches for value in batch)
        print(json.dumps({"concurrent_sessions": count, "requests": len(samples), "errors": 0,
                          "wall_seconds": round(time.monotonic() - start, 2),
                          "median_ms": round(statistics.median(samples), 1),
                          "p95_ms": round(samples[math.ceil(len(samples) * .95) - 1], 1),
                          "max_ms": round(samples[-1], 1)}))


if __name__ == "__main__":
    main()
