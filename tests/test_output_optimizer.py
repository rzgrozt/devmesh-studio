from __future__ import annotations

import asyncio
import json

from starlette.requests import Request

from devmesh_studio.runtime import mcp_http
from devmesh_studio.runtime.output_optimizer import MAX_MODEL_BYTES, OutputOptimizer, encoded_size


def test_large_log_is_bounded_without_changing_raw_result():
    raw = {"stdout": "INFO repeated line\n" * 4_000 + "FATAL: final error\n", "stderr": "", "returncode": 1}
    original = raw["stdout"]
    result = OutputOptimizer().optimize("terminal_exec", raw, call_id=42)

    assert result.optimized
    assert encoded_size(result.result) < MAX_MODEL_BYTES
    assert "FATAL: final error" in result.result["stdout"]
    assert "<<ccr:" not in str(result.result)
    assert result.result["_output_optimization"]["call_id"] == 42
    assert raw["stdout"] == original


def test_large_structured_result_has_hard_budget_and_no_retrieval_marker():
    raw = {"results": [{"path": f"file-{i}", "text": "needle " * 300} for i in range(1_000)]}
    result = OutputOptimizer().optimize("repo_search", raw)

    assert result.optimized
    assert encoded_size(result.result) < MAX_MODEL_BYTES
    assert "<<ccr:" not in str(result.result)
    assert result.result["_output_optimization"]["recorded_result_in_live_calls"] is False
    assert len(raw["results"]) == 1_000


def test_unicode_and_image_payload_stay_within_budget():
    raw = {"images": [{"type": "image", "data": "a" * 100_000}], "text": "🔥" * 40_000}
    result = OutputOptimizer().optimize("mcp_call", raw)

    assert encoded_size(result.result) < MAX_MODEL_BYTES
    assert "base64 image omitted" in str(result.result)
    assert len(raw["text"]) == 40_000


def test_small_result_is_unchanged():
    raw = {"text": "hello", "path": "README.md"}
    result = OutputOptimizer().optimize("fs_read", raw)
    assert not result.optimized
    assert result.result == raw


def test_mcp_response_sends_summary_and_keeps_review_diff_private(monkeypatch):
    raw = {"diff": "diff --git a/file b/file\n" + "+large line\n" * 5_000}

    class Context:
        def clear_last_call(self):
            pass

        def last_call_id(self):
            return 7

    class Registry:
        ctx = Context()

        async def dispatch(self, actor, name, arguments):
            return raw

    monkeypatch.setattr(mcp_http, "verify_access", lambda storage, token: {"sub": "tester"})
    router = mcp_http.create_mcp_router(object(), Registry(), OutputOptimizer())
    endpoint = next(route.endpoint for route in router.routes if route.path == "/mcp" and "POST" in route.methods)
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "git_diff", "arguments": {}}}).encode()

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    request = Request({"type": "http", "method": "POST", "path": "/mcp", "headers": [(b"authorization", b"Bearer test")]}, receive)
    response = asyncio.run(endpoint(request))
    result = json.loads(response.body)["result"]

    assert encoded_size(result["structuredContent"]["result"]) < MAX_MODEL_BYTES
    assert len(result["content"][0]["text"]) < 500
    assert result["_meta"]["devmeshFullResult"]["result"] == raw
    assert "_meta" not in result["structuredContent"]
