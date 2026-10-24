"""Minimal client for a *local* Ollama. Refuses any non-loopback host,
so file contents can't leave the machine by misconfiguration."""

from __future__ import annotations

import ipaddress
import json
import socket
import urllib.request
from urllib.parse import urlparse


class NotLocalError(ValueError):
    pass


def assert_loopback(host: str) -> None:
    hostname = urlparse(host).hostname or ""
    try:
        addrs = {info[4][0] for info in socket.getaddrinfo(hostname, None)}
    except socket.gaierror as e:
        raise NotLocalError(f"cannot resolve Ollama host {hostname!r}") from e
    if not addrs or not all(ipaddress.ip_address(a).is_loopback for a in addrs):
        raise NotLocalError(f"Ollama host {host} is not on this machine; refusing to send file contents there")


class Ollama:
    def __init__(self, host: str, timeout: float = 300):
        assert_loopback(host)
        self.host = host.rstrip("/")
        self.timeout = timeout

    def _request(self, path: str, payload: dict | None = None) -> dict:
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(
            self.host + path, data=data, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.load(resp)

    def version(self) -> str:
        return self._request("/api/version")["version"]

    def models(self) -> list[str]:
        return [m["name"] for m in self._request("/api/tags")["models"]]

    def chat(self, model: str, messages: list[dict], schema: dict | None = None,
             options: dict | None = None) -> tuple[str, dict]:
        """One non-streaming chat call. Returns (reply text, timing stats)."""
        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
            "think": False,  # we want a quick structured answer, not a reasoning trace
            "keep_alive": "15m",
            "options": {"temperature": 0, **(options or {})},
        }
        if schema is not None:
            payload["format"] = schema
        data = self._request("/api/chat", payload)
        stats = {
            "total_s": data.get("total_duration", 0) / 1e9,
            "load_s": data.get("load_duration", 0) / 1e9,
            "prompt_tokens": data.get("prompt_eval_count", 0),
            "output_tokens": data.get("eval_count", 0),
        }
        return data["message"]["content"], stats
