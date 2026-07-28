"""Focused checks for Tavily key rotation, failover, and response sanitizing."""
import tavily_search


class FakeResponse:
    def __init__(self, status_code=200, payload=None, headers=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.headers = headers or {}

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def ok(condition, message):
    print(("  OK  " if condition else " FAIL ") + message)
    if not condition:
        raise SystemExit(1)


def success_payload(query="测试"):
    return {
        "query": query,
        "results": [{
            "title": "示例来源",
            "url": "https://example.com/article?a=1",
            "content": "网页摘要",
            "score": 0.91,
        }],
        "usage": {"credits": 1},
        "response_time": "0.12",
        "request_id": "request-test",
    }


keys = tavily_search.parse_api_keys(" key-one,\nkey-two;key-one ")
ok(keys == ("key-one", "key-two"), "多行 Key 解析、去重并保留顺序")

round_robin_calls = []
round_robin_payloads = []


def round_robin_request(url, **kwargs):
    round_robin_calls.append(kwargs["headers"]["Authorization"])
    round_robin_payloads.append(kwargs["json"])
    return FakeResponse(payload=success_payload())


client = tavily_search.TavilySearchClient(
    ["key-one", "key-two"], request_func=round_robin_request,
)
client.search("第一次")
client.search("第二次")
ok(
    round_robin_calls == ["Bearer key-one", "Bearer key-two"],
    "成功请求在两条 Key 之间轮询",
)
ok(all(payload["safe_search"] is False for payload in round_robin_payloads),
   "免费 Key 默认使用 Tavily 官方的 safe_search=false")

failover_calls = []


def failover_request(url, **kwargs):
    auth = kwargs["headers"]["Authorization"]
    failover_calls.append(auth)
    if auth == "Bearer rate-limited":
        return FakeResponse(429, {"error": "limited"}, {"retry-after": "60"})
    return FakeResponse(payload=success_payload("切换成功"))


failover = tavily_search.TavilySearchClient(
    ["rate-limited", "healthy"], request_func=failover_request,
)
result = failover.search("失败切换")
ok(
    failover_calls == ["Bearer rate-limited", "Bearer healthy"]
    and result["query"] == "切换成功",
    "首条 Key 限流后自动尝试下一条",
)
failover.search("冷却跳过")
ok(failover_calls[-1] == "Bearer healthy", "限流 Key 冷却期间不会重复请求")

auth_calls = []


def rejected_request(url, **kwargs):
    auth_calls.append(kwargs["headers"]["Authorization"])
    return FakeResponse(401, {"detail": "secret should not escape"})


rejected = tavily_search.TavilySearchClient(
    ["private-one", "private-two"], request_func=rejected_request,
)
try:
    rejected.search("认证失败")
    raise SystemExit("expected TavilySearchError")
except tavily_search.TavilySearchError as exc:
    error_text = str(exc)
    error_code = exc.code
ok(
    len(auth_calls) == 2
    and "private-one" not in error_text
    and "private-two" not in error_text
    and error_code == "authentication",
    "所有 Key 失败时返回安全错误且不泄露密钥",
)


def unsafe_result_request(url, **kwargs):
    return FakeResponse(payload={
        "query": "清洗",
        "results": [
            {"title": "危险链接", "url": "javascript:alert(1)", "content": "x"},
            {"title": "正常链接", "url": "https://safe.example/path", "content": "a" * 3000},
        ],
    })


sanitized = tavily_search.TavilySearchClient(
    ["safe-key"], request_func=unsafe_result_request,
).search("清洗")
ok(
    len(sanitized["sources"]) == 1
    and sanitized["sources"][0]["url"].startswith("https://")
    and len(sanitized["sources"][0]["content"]) == 1800,
    "搜索结果只保留 HTTP(S) 来源并限制上下文长度",
)

print("\nAll web search checks passed.")
