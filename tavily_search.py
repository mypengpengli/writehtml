"""Small Tavily client with server-side key rotation and failover."""
from __future__ import annotations

import re
import threading
import time
from urllib.parse import urlparse

import httpx


class TavilySearchError(RuntimeError):
    """A user-safe search error that never contains an API key."""

    def __init__(self, message: str, code: str = "search_unavailable"):
        super().__init__(message)
        self.code = code


def parse_api_keys(values) -> tuple[str, ...]:
    """Parse comma/newline/semicolon separated keys and preserve their order."""
    if isinstance(values, str):
        values = re.split(r"[,;\r\n]+", values)
    if not isinstance(values, (list, tuple)):
        return ()
    keys = []
    for value in values:
        if not isinstance(value, str):
            continue
        key = value.strip().strip("\"'")
        if key and key not in keys:
            keys.append(key)
    return tuple(keys)


def _clean_text(value, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    text = re.sub(r"\s+", " ", value).strip()
    return text[:limit]


def _safe_url(value) -> str:
    if not isinstance(value, str):
        return ""
    value = value.strip()
    try:
        parsed = urlparse(value)
    except ValueError:
        return ""
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return value[:3000]


class TavilySearchClient:
    """Thread-safe round-robin key pool.

    A failed key is put on a short cooldown so subsequent turns do not repeatedly
    hit a known rate-limit, quota, authentication, or transient service failure.
    """

    endpoint = "https://api.tavily.com/search"

    def __init__(
        self,
        api_keys,
        *,
        timeout_seconds: float = 20,
        project_id: str = "",
        search_depth: str = "basic",
        safe_search: bool = False,
        request_func=None,
    ):
        self._keys = parse_api_keys(api_keys)
        self.timeout_seconds = max(1.0, float(timeout_seconds))
        self.project_id = (project_id or "").strip()[:200]
        self.search_depth = search_depth if search_depth in {"basic", "advanced"} else "basic"
        self.safe_search = bool(safe_search)
        self._request = request_func or httpx.post
        self._lock = threading.Lock()
        self._cursor = 0
        self._cooldowns = {}

    @property
    def key_count(self) -> int:
        return len(self._keys)

    @property
    def configured(self) -> bool:
        return bool(self._keys)

    def _candidate_indices(self) -> list[int]:
        now = time.monotonic()
        with self._lock:
            if not self._keys:
                return []
            start = self._cursor % len(self._keys)
            self._cursor = (self._cursor + 1) % len(self._keys)
            ordered = [(start + offset) % len(self._keys) for offset in range(len(self._keys))]
            return [index for index in ordered if self._cooldowns.get(index, 0) <= now]

    def _cooldown(self, index: int, seconds: float):
        with self._lock:
            self._cooldowns[index] = max(
                self._cooldowns.get(index, 0),
                time.monotonic() + max(1.0, seconds),
            )

    @staticmethod
    def _retry_after(response, default: float) -> float:
        try:
            value = float(response.headers.get("retry-after", default))
        except (TypeError, ValueError):
            value = default
        return min(600.0, max(1.0, value))

    def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        topic: str = "general",
        time_range: str | None = None,
    ) -> dict:
        query = _clean_text(query, 500)
        if not query:
            raise TavilySearchError("搜索关键词不能为空", "invalid_query")
        if not self._keys:
            raise TavilySearchError(
                "联网搜索尚未配置，请在“模型与联网设置”中填写 Tavily API Key",
                "not_configured",
            )
        max_results = min(10, max(1, int(max_results)))
        topic = topic if topic in {"general", "news"} else "general"
        time_range = time_range if time_range in {"day", "week", "month", "year"} else None
        payload = {
            "query": query,
            "search_depth": self.search_depth,
            "max_results": max_results,
            "topic": topic,
            "include_answer": False,
            "include_raw_content": False,
            "include_images": False,
            "include_usage": True,
            "safe_search": self.safe_search,
        }
        if time_range:
            payload["time_range"] = time_range

        candidates = self._candidate_indices()
        if not candidates:
            raise TavilySearchError(
                "所有 Tavily Key 正在限流或冷却，请稍后再试",
                "all_keys_cooling_down",
            )

        failures = []
        for index in candidates:
            headers = {
                "Authorization": f"Bearer {self._keys[index]}",
                "Content-Type": "application/json",
            }
            if self.project_id:
                headers["X-Project-ID"] = self.project_id
            try:
                response = self._request(
                    self.endpoint,
                    headers=headers,
                    json=payload,
                    timeout=self.timeout_seconds,
                )
            except (httpx.HTTPError, OSError, TimeoutError):
                failures.append("network")
                self._cooldown(index, 15)
                continue

            status = int(response.status_code)
            if status == 200:
                try:
                    data = response.json()
                except ValueError:
                    failures.append("invalid_response")
                    self._cooldown(index, 30)
                    continue
                if not isinstance(data, dict):
                    failures.append("invalid_response")
                    self._cooldown(index, 30)
                    continue
                return self._normalize_response(query, data)
            if status == 400:
                raise TavilySearchError("Tavily 拒绝了搜索参数，请换一个更简短的查询", "bad_request")
            if status in {401, 403}:
                failures.append("authentication")
                self._cooldown(index, 1800)
                continue
            if status == 429:
                failures.append("rate_limit")
                self._cooldown(index, self._retry_after(response, 60))
                continue
            if status in {432, 433}:
                failures.append("quota")
                self._cooldown(index, 600)
                continue
            if status >= 500 or status in {408, 425}:
                failures.append("provider")
                self._cooldown(index, 20)
                continue
            raise TavilySearchError(f"Tavily 搜索请求失败（HTTP {status}）", "request_failed")

        if failures and all(item == "authentication" for item in failures):
            raise TavilySearchError("所有 Tavily Key 均无效或无权访问搜索接口", "authentication")
        if "quota" in failures:
            raise TavilySearchError("可用 Tavily Key 的套餐额度已用完或受到限制", "quota")
        if "rate_limit" in failures:
            raise TavilySearchError("所有 Tavily Key 当前均被限流，请稍后再试", "rate_limit")
        raise TavilySearchError("Tavily 搜索服务暂时不可用，请稍后再试", "provider_unavailable")

    @staticmethod
    def _normalize_response(query: str, data: dict) -> dict:
        sources = []
        for item in data.get("results") or []:
            if not isinstance(item, dict):
                continue
            url = _safe_url(item.get("url"))
            if not url:
                continue
            score = item.get("score")
            sources.append({
                "title": _clean_text(item.get("title"), 300) or urlparse(url).netloc,
                "url": url,
                "content": _clean_text(item.get("content"), 1800),
                "score": round(float(score), 4) if isinstance(score, (int, float)) else None,
            })
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        credits = usage.get("credits")
        return {
            "query": _clean_text(data.get("query"), 500) or query,
            "sources": sources,
            "response_time": _clean_text(str(data.get("response_time") or ""), 30),
            "request_id": _clean_text(data.get("request_id"), 120),
            "credits": int(credits) if isinstance(credits, (int, float)) else None,
        }
