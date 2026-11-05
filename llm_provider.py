from __future__ import annotations

from dataclasses import dataclass, field
import http.client
import json
import os
from pathlib import Path
import re
import stat
import time


class ModelError(Exception):
    def __init__(self, code, usage=None):
        super().__init__(code)
        self.code, self.usage = code, usage


@dataclass(frozen=True)
class ModelSettings:
    enabled: bool = False
    key_file: str = field(default="", repr=False)
    model: str = "deepseek-flash"
    source_ids: tuple = ("product", "delivery", "faq")
    timeout: int = 12
    max_tokens: int = 1024
    daily_tokens: int = 120000
    user_daily_tokens: int = 40000
    daily_requests: int = 40
    user_daily_requests: int = 10

    def __post_init__(self):
        if self.model not in {"deepseek-flash", "deepseek-v4-pro"}:
            raise ValueError("Unsupported DeepSeek model; check the official model list.")
        for name, low, high in (("timeout", 2, 20), ("max_tokens", 64, 2048),
                                ("daily_tokens", 1000, 1000000), ("user_daily_tokens", 1000, 200000),
                                ("daily_requests", 1, 200), ("user_daily_requests", 1, 50)):
            value = getattr(self, name)
            if type(value) is not int or not low <= value <= high:
                raise ValueError("Invalid AI limit: " + name)
        if any(not isinstance(item, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", item) for item in self.source_ids):
            raise ValueError("Invalid AI source allowlist.")

    @classmethod
    def from_environment(cls):
        values = {"enabled": os.getenv("WORKBENCH_AI_ENABLED", "0") == "1",
                  "key_file": os.getenv("WORKBENCH_AI_KEY_FILE", ""),
                  "model": os.getenv("WORKBENCH_AI_MODEL", "deepseek-flash"),
                  "source_ids": tuple(filter(None, os.getenv("WORKBENCH_AI_SOURCE_IDS", "product,delivery,faq").split(",")))}
        names = {"timeout": "TIMEOUT", "max_tokens": "MAX_OUTPUT_TOKENS", "daily_tokens": "DAILY_TOKENS",
                 "user_daily_tokens": "USER_DAILY_TOKENS", "daily_requests": "DAILY_REQUESTS",
                 "user_daily_requests": "USER_DAILY_REQUESTS"}
        for name, suffix in names.items():
            raw = os.getenv("WORKBENCH_AI_" + suffix)
            if raw is not None:
                try:
                    values[name] = int(raw)
                except ValueError:
                    raise ValueError("Invalid AI limit: " + name) from None
        return cls(**values)


def read_key(path):
    # Refuse symlinks and broadly readable files; never include the value/path in errors.
    try:
        if not path or not Path(path).is_absolute():
            raise OSError()
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "r", encoding="ascii") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_uid not in {0, os.geteuid()} or info.st_size > 256 or info.st_nlink != 1:
                raise OSError()
            key = stream.read(257).strip()
        if not re.fullmatch(r"sk-[A-Za-z0-9_-]{16,200}", key):
            raise ValueError()
        return key
    except (OSError, UnicodeError, ValueError):
        raise ModelError("AI_KEY_UNAVAILABLE") from None


class DeepSeekProvider:
    def __init__(self, settings, transport=None):
        self.settings = settings
        self.transport = transport or self._transport

    def _transport(self, payload, key):
        deadline = time.monotonic() + self.settings.timeout
        conn = http.client.HTTPSConnection("api.deepseek.com", timeout=self.settings.timeout)
        try:
            conn.connect()
            conn.sock.settimeout(max(0.01, deadline - time.monotonic()))
            conn.request("POST", "/chat/completions", payload,
                         {"Authorization": "Bearer " + key, "Content-Type": "application/json", "Accept-Encoding": "identity"})
            conn.sock.settimeout(max(0.01, deadline - time.monotonic()))
            response = conn.getresponse()
            chunks, size = [], 0
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError()
                if conn.sock:
                    conn.sock.settimeout(remaining)
                chunk = response.read1(4096)
                if not chunk:
                    break
                size += len(chunk)
                if size > 131072:
                    raise ModelError("AI_RESPONSE_TOO_LARGE")
                chunks.append(chunk)
            return response.status, b"".join(chunks)
        finally:
            conn.close()

    @staticmethod
    def usage(data):
        usage = data.get("usage")
        if not isinstance(usage, dict):
            return None
        values = [usage.get(name) for name in ("prompt_tokens", "completion_tokens", "total_tokens")]
        if any(type(value) is not int or not 0 <= value <= 2000000 for value in values) or values[2] != values[0] + values[1]:
            return None
        return dict(zip(("input_tokens", "output_tokens", "total_tokens"), values))

    def complete(self, messages, citation_count):
        key = read_key(self.settings.key_file)
        payload = json.dumps({"model": self.settings.model, "messages": messages,
                              "thinking": {"type": "disabled"}, "stream": False, "temperature": 0.2,
                              "response_format": {"type": "json_object"}, "max_tokens": self.settings.max_tokens}, ensure_ascii=False).encode()
        try:
            status, raw = self.transport(payload, key)
        except ModelError:
            raise
        except (TimeoutError, OSError, http.client.HTTPException):
            raise ModelError("AI_TIMEOUT_OR_NETWORK") from None
        if status != 200:
            code = {401: "AI_AUTH_FAILED", 402: "AI_BALANCE_EMPTY", 429: "AI_PROVIDER_RATE_LIMIT"}.get(status, "AI_PROVIDER_ERROR")
            raise ModelError(code)
        if not isinstance(raw, bytes) or len(raw) > 131072:
            raise ModelError("AI_RESPONSE_TOO_LARGE")
        usage = None
        try:
            data = json.loads(raw)
            usage = self.usage(data)
            choice = data["choices"][0]
            if choice["finish_reason"] != "stop" or choice["message"].get("tool_calls"):
                raise ValueError()
            content = choice["message"]["content"]
            if not isinstance(content, str) or len(content) > 16000:
                raise ValueError()
            output = json.loads(content)
            if not isinstance(output, dict) or set(output) != {"text", "citations", "insufficient"}:
                raise ValueError()
            if not isinstance(output["text"], str) or not 1 <= len(output["text"].strip()) <= 2400 or type(output["insufficient"]) is not bool:
                raise ValueError()
            citations = output["citations"]
            if not isinstance(citations, list) or len(citations) > citation_count or any(type(item) is not int or not 1 <= item <= citation_count for item in citations) or len(set(citations)) != len(citations):
                raise ValueError()
            if not output["insufficient"] and not citations:
                raise ValueError()
            if any(int(marker) not in citations for marker in re.findall(r"\[(\d+)\]", output["text"])):
                raise ValueError()
            output["text"] = output["text"].strip()
        except (ValueError, TypeError, KeyError, IndexError, AttributeError, UnicodeError):
            raise ModelError("AI_INVALID_RESPONSE", usage) from None
        return output, usage
