from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from typing import Any

from .config import env_value


class SupabaseStore:
    def __init__(self) -> None:
        self.url = env_value("SUPABASE_URL").rstrip("/")
        self.key = env_value("SUPABASE_SERVICE_ROLE_KEY") or env_value("SUPABASE_ANON_KEY")

    @property
    def enabled(self) -> bool:
        return bool(self.url and self.key)

    def select(self, table: str, *, query: dict[str, str] | None = None) -> list[dict[str, Any]]:
        return self._request("GET", table, query=query or {})

    def insert(self, table: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return self._request("POST", table, payload=rows, prefer="return=representation")

    def upsert(self, table: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return self._request(
            "POST",
            table,
            payload=rows,
            prefer="resolution=merge-duplicates,return=representation",
        )

    def patch(self, table: str, *, query: dict[str, str], values: dict[str, Any]) -> list[dict[str, Any]]:
        return self._request("PATCH", table, query=query, payload=values, prefer="return=representation")

    def _request(
        self,
        method: str,
        table: str,
        *,
        query: dict[str, str] | None = None,
        payload: Any | None = None,
        prefer: str = "",
    ) -> list[dict[str, Any]]:
        if not self.enabled:
            raise RuntimeError("Supabase is not configured")
        params = urllib.parse.urlencode(query or {})
        endpoint = f"{self.url}/rest/v1/{table}"
        if params:
            endpoint += f"?{params}"
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {
            "apikey": self.key,
            "Authorization": f"Bearer {self.key}",
            "Content-Type": "application/json",
        }
        if prefer:
            headers["Prefer"] = prefer
        request = urllib.request.Request(endpoint, data=data, headers=headers, method=method)
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read().decode("utf-8")
        if not body:
            return []
        parsed = json.loads(body)
        return parsed if isinstance(parsed, list) else [parsed]


def store() -> SupabaseStore:
    return SupabaseStore()


def utc_timestamp() -> float:
    return time.time()
