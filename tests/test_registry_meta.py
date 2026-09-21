from __future__ import annotations
import asyncio
from jsonschema import validate
from devmesh_studio.runtime.tool_registry import REVIEW_WIDGET_TOOLS, TOOL_DEFS, TOOL_OUTPUT_SCHEMA, ToolRegistry


def test_tool_registry_is_unique_and_complete():
    names=[x["name"] for x in TOOL_DEFS]
    assert len(names)==55
    assert len(names)==len(set(names))
    for item in TOOL_DEFS:
        assert item["description"]
        assert item["inputSchema"]["type"]=="object"
        assert item["outputSchema"]["type"]=="object"
        assert set(item["outputSchema"]["required"]) == {"tool", "result", "status", "duration_ms", "usage"}
        assert "annotations" in item
        assert ("_meta" in item) is (item["name"] in REVIEW_WIDGET_TOOLS)


def test_common_output_schema_accepts_every_result_state():
    usage = {"input_tokens": 4, "output_tokens": 8, "estimated": True}
    for status, result in (
        ("ok", {"path": "src/app.py", "bytes": 42}),
        ("approval_required", {"approval_required": True, "approval_id": 7}),
        ("error", {"error": "ValueError: invalid path"}),
    ):
        validate(
            {"tool": "fs_write", "result": result, "status": status, "duration_ms": 12, "usage": usage},
            TOOL_OUTPUT_SCHEMA,
        )


def test_history_repo_stats_and_approval_tools(repo_env):
    storage, repo, root = repo_env
    storage.upsert_permission(repo["id"],"edit","*","ask",200)
    registry=ToolRegistry(storage)
    async def run():
        await registry.dispatch("chatgpt","fs_read",{"repo_id":repo["id"],"path":"README.md"})
        try:
            await registry.dispatch("chatgpt","fs_edit",{"repo_id":repo["id"],"path":"README.md","old":"devmesh","new":"mesh"})
        except Exception:
            pass
        stats=await registry.dispatch("chatgpt","repo_stats",{"repo_id":repo["id"]})
        calls=await registry.dispatch("chatgpt","history_calls",{"repo_id":repo["id"],"limit":20})
        approvals=await registry.dispatch("chatgpt","approval_list",{"status":"pending"})
        processes=await registry.dispatch("chatgpt","process_list",{})
        return stats,calls,approvals,processes
    stats,calls,approvals,processes=asyncio.run(run())
    assert stats["calls"] >= 1
    assert calls["calls"]
    assert approvals["approvals"]
    assert isinstance(processes["processes"],list)
