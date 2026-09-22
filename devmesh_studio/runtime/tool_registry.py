from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Callable

from devmesh_studio.core.permissions import ApprovalRequired
from devmesh_studio.core.storage import Storage
from devmesh_studio.tools.agents import AgentTools
from devmesh_studio.tools.base import ToolContext
from devmesh_studio.tools.code_intel import CodeIntelTools
from devmesh_studio.tools.downstream_mcp import DownstreamMCPTools
from devmesh_studio.tools.filesystem import FilesystemTools
from devmesh_studio.tools.git import GitTools
from devmesh_studio.tools.skills import SkillTools
from devmesh_studio.tools.tasks import TaskTools
from devmesh_studio.tools.terminal import TerminalTools
from .auth import public_url
from .live_activity import ActivityScope, LiveActivity
from .live_widget import LIVE_WIDGET_URI
from .tool_widget import TOOL_WIDGET_URI


RO = {"readOnlyHint": True, "openWorldHint": False, "destructiveHint": False}
RW = {"readOnlyHint": False, "openWorldHint": False, "destructiveHint": False, "idempotentHint": False}
DANGER = {"readOnlyHint": False, "openWorldHint": False, "destructiveHint": True, "idempotentHint": False}
OPEN_RO = {"readOnlyHint": True, "openWorldHint": True, "destructiveHint": False}
OPEN_DANGER = {"readOnlyHint": False, "openWorldHint": True, "destructiveHint": True, "idempotentHint": False}

# Keep the chat surface quiet.  As in DevSpace, the rich UI is a review
# surface, not a second rendering of every tool call.  Read/search/status and
# command tools remain native, compact ChatGPT tool rows.
REVIEW_WIDGET_TOOLS = {
    "fs_write",
    "fs_edit",
    "patch_preview",
    "patch_apply",
    "patch_revert",
    "git_diff",
    "git_show",
}


@dataclass
class ToolDispatchResult:
    data: dict[str, Any]
    private_meta: dict[str, Any]


def obj(properties: dict[str, Any] | None = None, required: list[str] | None = None) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "object", "properties": properties or {}}
    if required:
        schema["required"] = required
    return schema


REPO = {"repo_id": {"type": "integer", "description": "Repository id returned by repo_list."}}

TOOL_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "tool": {"type": "string", "description": "The DevMesh tool that produced this result."},
        "result": {
            "type": "object",
            "description": "Tool-specific structured result, approval request, or error details.",
            "additionalProperties": True,
        },
        "status": {
            "type": "string",
            "enum": ["ok", "approval_required", "error"],
        },
        "duration_ms": {"type": "integer", "minimum": 0},
        "usage": {
            "type": "object",
            "properties": {
                "input_tokens": {"type": "integer", "minimum": 0},
                "output_tokens": {"type": "integer", "minimum": 0},
                "estimated": {"type": "boolean", "const": True},
            },
            "required": ["input_tokens", "output_tokens", "estimated"],
            "additionalProperties": False,
        },
    },
    "required": ["tool", "result", "status", "duration_ms", "usage"],
    "additionalProperties": False,
}


def tool(name: str, description: str, schema: dict[str, Any], annotations: dict[str, Any]) -> dict[str, Any]:
    definition = {
        "name": name,
        "title": name.replace("_", " ").title(),
        "description": description,
        "inputSchema": schema,
        "outputSchema": TOOL_OUTPUT_SCHEMA,
        "annotations": annotations,
    }
    if name in REVIEW_WIDGET_TOOLS:
        definition["_meta"] = {
            "ui": {"resourceUri": TOOL_WIDGET_URI},
            "openai/outputTemplate": TOOL_WIDGET_URI,
            "openai/toolInvocation/invoking": "DevMesh is working…",
            "openai/toolInvocation/invoked": "DevMesh finished",
        }
    elif name == "live_activity_open":
        definition["_meta"] = {
            "ui": {"resourceUri": LIVE_WIDGET_URI},
            "openai/outputTemplate": LIVE_WIDGET_URI,
            "openai/toolInvocation/invoking": "Opening DevMesh Live…",
            "openai/toolInvocation/invoked": "DevMesh Live is ready",
        }
    return definition


TOOL_DEFS = [
    tool("repo_list", "List repositories explicitly registered in DevMesh.", obj(), RO),
    tool("repo_info", "Return repository metadata, Git branch/HEAD, and file count.", obj(REPO, ["repo_id"]), RO),
    tool("repo_stats", "Return repository activity statistics from DevMesh history.", obj(REPO, ["repo_id"]), RO),
    tool("repo_recent_changes", "Return recent DevMesh patches and Git status for a repository.", obj({**REPO, "limit": {"type":"integer","default":30,"minimum":1,"maximum":200}}, ["repo_id"]), RO),

    tool("fs_read", "Read a UTF-8 text file with bounded line ranges. Binary files return metadata only.", obj({**REPO,"path":{"type":"string"},"start_line":{"type":"integer","default":1,"minimum":1},"end_line":{"type":"integer","minimum":1}}, ["repo_id","path"]), RO),
    tool("fs_read_many", "Read several text files in one call.", obj({**REPO,"paths":{"type":"array","items":{"type":"string"},"maxItems":50}}, ["repo_id","paths"]), RO),
    tool("fs_stat", "Return file or directory metadata.", obj({**REPO,"path":{"type":"string"}}, ["repo_id","path"]), RO),
    tool("fs_list", "List direct children of a directory.", obj({**REPO,"path":{"type":"string","default":"."},"offset":{"type":"integer","default":0},"limit":{"type":"integer","default":300,"maximum":1000}}, ["repo_id"]), RO),
    tool("fs_tree", "Return a bounded repository directory tree.", obj({**REPO,"path":{"type":"string","default":"."},"max_depth":{"type":"integer","default":3,"minimum":1,"maximum":8},"max_entries":{"type":"integer","default":1000,"maximum":5000}}, ["repo_id"]), RO),
    tool("fs_glob", "Find repository paths matching a glob pattern.", obj({**REPO,"pattern":{"type":"string"},"max_results":{"type":"integer","default":500,"maximum":2000}}, ["repo_id","pattern"]), RO),
    tool("fs_grep", "Search repository text using ripgrep when available.", obj({**REPO,"query":{"type":"string"},"glob":{"type":"string"},"regex":{"type":"boolean","default":False},"max_results":{"type":"integer","default":200,"maximum":1000}}, ["repo_id","query"]), RO),
    tool("fs_write", "Create or replace a UTF-8 file inside a repository. Records a reversible patch.", obj({**REPO,"path":{"type":"string"},"content":{"type":"string"},"overwrite":{"type":"boolean","default":True}}, ["repo_id","path","content"]), RW),
    tool("fs_edit", "Perform exact text replacement in a file. Refuses ambiguous replacements unless replace_all is true.", obj({**REPO,"path":{"type":"string"},"old":{"type":"string"},"new":{"type":"string"},"replace_all":{"type":"boolean","default":False}}, ["repo_id","path","old","new"]), RW),
    tool("patch_preview", "Validate a unified Git patch without applying it.", obj({**REPO,"patch":{"type":"string"}}, ["repo_id","patch"]), RO),
    tool("patch_apply", "Apply a validated unified Git patch confined to the repository.", obj({**REPO,"patch":{"type":"string"}}, ["repo_id","patch"]), RW),
    tool("patch_revert", "Reverse a previously recorded DevMesh patch when it still applies cleanly.", obj({"patch_id":{"type":"integer"}}, ["patch_id"]), RW),

    tool("terminal_exec", "Run a command in the repository. Prefer argv for shell-free execution; command enables native-shell pipelines/redirection.", obj({**REPO,"argv":{"type":"array","items":{"type":"string"}},"command":{"type":"string"},"timeout":{"type":"integer","default":120,"maximum":900},"env":{"type":"object","additionalProperties":{"type":"string"}}}, ["repo_id"]), DANGER),
    tool("terminal_start", "Start a persistent native terminal session in a repository (PTY on Unix, redirected process on Windows).", obj({**REPO,"command":{"type":"string","default":""}}, ["repo_id"]), DANGER),
    tool("terminal_write", "Write input to a persistent terminal session.", obj({"session_id":{"type":"string"},"data":{"type":"string"}}, ["session_id","data"]), DANGER),
    tool("terminal_read", "Read accumulated output from a persistent PTY session.", obj({"session_id":{"type":"string"},"clear":{"type":"boolean","default":True}}, ["session_id"]), RO),
    tool("terminal_kill", "Terminate a persistent PTY session.", obj({"session_id":{"type":"string"}}, ["session_id"]), DANGER),
    tool("process_list", "List terminal processes created through DevMesh.", obj(), RO),

    tool("git_status", "Return structured Git status text and repository metadata.", obj(REPO, ["repo_id"]), RO),
    tool("git_diff", "Return Git diff for working tree or index.", obj({**REPO,"staged":{"type":"boolean","default":False},"path":{"type":"string"}}, ["repo_id"]), RO),
    tool("git_log", "Return recent Git commits.", obj({**REPO,"limit":{"type":"integer","default":30,"maximum":200}}, ["repo_id"]), RO),
    tool("git_show", "Show one Git revision and patch.", obj({**REPO,"ref":{"type":"string","default":"HEAD"},"path":{"type":"string"}}, ["repo_id"]), RO),
    tool("git_branches", "List local and remote branches.", obj(REPO, ["repo_id"]), RO),
    tool("git_checkout", "Switch branches or create a new branch.", obj({**REPO,"branch":{"type":"string"},"create":{"type":"boolean","default":False}}, ["repo_id","branch"]), DANGER),
    tool("git_add", "Stage repository paths.", obj({**REPO,"paths":{"type":"array","items":{"type":"string"},"minItems":1}}, ["repo_id","paths"]), RW),
    tool("git_commit", "Create a Git commit from staged changes.", obj({**REPO,"message":{"type":"string"}}, ["repo_id","message"]), DANGER),
    tool("git_restore", "Restore paths in worktree or index.", obj({**REPO,"paths":{"type":"array","items":{"type":"string"},"minItems":1},"staged":{"type":"boolean","default":False}}, ["repo_id","paths"]), DANGER),
    tool("git_push", "Push Git changes. Default DevMesh permissions require desktop approval.", obj({**REPO,"remote":{"type":"string","default":"origin"},"branch":{"type":"string"}}, ["repo_id"]), DANGER),

    tool("task_list", "Detect common test/build/lint/typecheck tasks from project files.", obj(REPO, ["repo_id"]), RO),
    tool("task_run", "Run one detected named project task; arbitrary commands are not accepted.", obj({**REPO,"name":{"type":"string"},"timeout":{"type":"integer","default":600,"maximum":1800}}, ["repo_id","name"]), DANGER),

    tool("code_symbols", "List source-file symbols. Uses a detected Language Server when available, then falls back to local AST/syntax analysis.", obj({**REPO,"path":{"type":"string"}}, ["repo_id","path"]), RO),
    tool("code_references", "Search repository references to a symbol. This compatibility tool uses bounded text search; use code_references_at for semantic LSP references.", obj({**REPO,"symbol":{"type":"string"},"max_results":{"type":"integer","default":200,"maximum":1000}}, ["repo_id","symbol"]), RO),
    tool("code_definition", "Find symbol definitions, preferring LSP workspace symbols when available and otherwise using syntax search.", obj({**REPO,"symbol":{"type":"string"}}, ["repo_id","symbol"]), RO),
    tool("code_hover", "Return semantic hover/type/documentation from the repository Language Server. line is 1-based; character is 0-based.", obj({**REPO,"path":{"type":"string"},"line":{"type":"integer","minimum":1},"character":{"type":"integer","default":0,"minimum":0}}, ["repo_id","path","line"]), RO),
    tool("code_definition_at", "Resolve the semantic definition at a source position using LSP. line is 1-based; character is 0-based.", obj({**REPO,"path":{"type":"string"},"line":{"type":"integer","minimum":1},"character":{"type":"integer","default":0,"minimum":0}}, ["repo_id","path","line"]), RO),
    tool("code_references_at", "Resolve semantic references at a source position using LSP. line is 1-based; character is 0-based.", obj({**REPO,"path":{"type":"string"},"line":{"type":"integer","minimum":1},"character":{"type":"integer","default":0,"minimum":0},"include_declaration":{"type":"boolean","default":True}}, ["repo_id","path","line"]), RO),
    tool("code_diagnostics", "Return Language Server diagnostics for a source file, including severity, code, source and ranges.", obj({**REPO,"path":{"type":"string"},"wait_ms":{"type":"integer","default":600,"minimum":0,"maximum":5000}}, ["repo_id","path"]), RO),
    tool("code_workspace_symbols", "Search semantic workspace symbols across detected Language Servers in a repository.", obj({**REPO,"query":{"type":"string"},"max_results":{"type":"integer","default":200,"minimum":1,"maximum":1000}}, ["repo_id","query"]), RO),
    tool("code_call_hierarchy", "Return incoming or outgoing semantic call hierarchy at a source position using LSP.", obj({**REPO,"path":{"type":"string"},"line":{"type":"integer","minimum":1},"character":{"type":"integer","default":0,"minimum":0},"direction":{"type":"string","enum":["incoming","outgoing"],"default":"incoming"}}, ["repo_id","path","line"]), RO),
    tool("code_lsp_status", "Show detected/configured language servers and active repository-scoped LSP processes.", obj(), RO),

    tool("skill_list", "Discover compatible SKILL.md files in repository/global Codex, Claude, OpenCode and .agents locations.", obj({"repo_id":{"type":"integer"}}), RO),
    tool("skill_read", "Read one discovered coding skill.", obj({"repo_id":{"type":"integer"},"name":{"type":"string"}}, ["name"]), RO),
    tool("skill_search", "Search discovered skills by name or description.", obj({"repo_id":{"type":"integer"},"query":{"type":"string"}}, ["query"]), RO),

    tool("agent_list", "List locally installed supported coding-agent CLIs.", obj(), RO),
    tool("agent_delegate", "Delegate a bounded task to an installed Codex/Claude Code/OpenCode CLI using fixed argv templates.", obj({**REPO,"agent":{"type":"string","enum":["codex","claude","opencode"]},"task":{"type":"string"},"mode":{"type":"string","enum":["propose","apply"],"default":"propose"},"timeout":{"type":"integer","default":900,"maximum":3600}}, ["repo_id","agent","task"]), DANGER),

    tool("mcp_list", "List downstream MCP servers explicitly registered in DevMesh. Secret environment values are never returned.", obj(), OPEN_RO),
    tool("mcp_tools", "Connect to a registered downstream MCP and list its tool schemas and allowlist state.", obj({"server_id":{"oneOf":[{"type":"integer"},{"type":"string"}]}}, ["server_id"]), OPEN_RO),
    tool("mcp_call", "Invoke an explicitly allowlisted tool on a registered downstream MCP server.", obj({"repo_id":{"type":"integer"},"server_id":{"oneOf":[{"type":"integer"},{"type":"string"}]},"tool_name":{"type":"string"},"arguments":{"type":"object","additionalProperties":True}}, ["server_id","tool_name","arguments"]), OPEN_DANGER),
    tool("live_activity_open", "Open one read-only live activity console scoped to this actor and optional repository, downstream MCP server, and public session label.", obj({"repo_id":{"type":"integer"},"server_id":{"oneOf":[{"type":"integer"},{"type":"string"}]},"session":{"type":"string","maxLength":80}}), OPEN_RO),

    tool("history_calls", "Inspect recent DevMesh tool-call history.", obj({"repo_id":{"type":"integer"},"limit":{"type":"integer","default":100,"maximum":500}}), RO),
    tool("history_patches", "Inspect recorded DevMesh patch history.", obj({"repo_id":{"type":"integer"},"limit":{"type":"integer","default":100,"maximum":500}}), RO),
    tool("approval_list", "List pending or recent approval requests visible in DevMesh Desktop.", obj({"status":{"type":"string","enum":["pending","approved_once","approved_always","denied","consumed","all"],"default":"pending"}}), RO),
]


class ToolRegistry:
    def __init__(self, storage: Storage, live_activity: LiveActivity | None = None):
        self.storage = storage
        self.ctx = ToolContext(storage)
        self.fs = FilesystemTools(self.ctx)
        self.git = GitTools(self.ctx)
        self.terminal = TerminalTools(self.ctx)
        self.tasks = TaskTools(self.ctx)
        self.code = CodeIntelTools(self.ctx)
        self.skills = SkillTools(self.ctx)
        self.agents = AgentTools(self.ctx)
        self.live_activity = live_activity or LiveActivity()
        self.mcp = DownstreamMCPTools(self.ctx, self.live_activity)

    def definitions(self) -> list[dict[str, Any]]:
        return TOOL_DEFS

    async def dispatch(self, actor: str, name: str, a: dict[str, Any]) -> Any:
        rid = a.get("repo_id")
        fn: Callable[..., Any] | None = None
        kwargs: dict[str, Any] = dict(a)

        if name == "live_activity_open":
            repo_id = a.get("repo_id")
            requested_server = a.get("server_id")
            server = None
            if requested_server is not None:
                server = self.storage.get_mcp_server(requested_server)
                if not server or not server["enabled"]:
                    raise KeyError(f"unknown/disabled MCP server: {requested_server}")
            session = a.get("session")
            if session is not None:
                session = str(session).replace("\r", " ").replace("\n", " ").strip()[:80] or None
            scope = ActivityScope(
                actor=actor, repo_id=repo_id,
                server_id=int(server["id"]) if server else None,
                session=session,
            )
            self.ctx.clear_last_call()
            record_args = {"repo_id": repo_id, "server_id": requested_server, "session": session}
            with self.ctx.record(actor, repo_id, "live_activity_open", "live_activity", record_args):
                capability, expires_in = await self.live_activity.mint(scope)
            origin = public_url(self.storage)
            server_name = server["name"] if server else "downstream MCP"
            return ToolDispatchResult(
                data={"status": "ready", "server": server_name},
                private_meta={"devmeshLive": {
                    "capability": capability,
                    "expiresIn": expires_in,
                    "streamUrl": origin + "/live/activity/stream",
                    "previewBaseUrl": origin + "/live/activity/preview",
                    "serverName": server_name,
                    "session": session,
                }},
            )

        if name == "repo_list":
            return {"repositories": [self.ctx.repos.info(r["id"]) for r in self.storage.list_repositories(enabled_only=True)]}
        if name == "repo_info":
            return self.ctx.repos.info(rid)
        if name == "repo_stats":
            calls = self.storage.list_calls(5000, rid)
            patches = self.storage.list_patches(5000, rid)
            return {
                "repo": self.ctx.repos.info(rid),
                "calls": len(calls),
                "successful_calls": sum(c["status"] == "ok" for c in calls),
                "patches": len(patches),
                "reverted_patches": sum(bool(p["reverted"]) for p in patches),
            }
        if name == "repo_recent_changes":
            limit = int(a.get("limit", 30))
            return {"status": self.git.status(actor, rid), "patches": self.storage.list_patches(limit, rid)}

        mapping: dict[str, tuple[Callable[..., Any], list[str]]] = {
            "fs_read": (self.fs.read, ["repo_id","path","start_line","end_line"]),
            "fs_read_many": (self.fs.read_many, ["repo_id","paths"]),
            "fs_stat": (self.fs.stat, ["repo_id","path"]),
            "fs_list": (self.fs.list_dir, ["repo_id","path","offset","limit"]),
            "fs_tree": (self.fs.tree, ["repo_id","path","max_depth","max_entries"]),
            "fs_glob": (self.fs.glob, ["repo_id","pattern","max_results"]),
            "fs_grep": (self.fs.grep, ["repo_id","query","glob","regex","max_results"]),
            "fs_write": (self.fs.write, ["repo_id","path","content","overwrite"]),
            "fs_edit": (self.fs.edit, ["repo_id","path","old","new","replace_all"]),
            "patch_preview": (self.fs.patch_preview, ["repo_id","patch"]),
            "patch_apply": (self.fs.apply_patch, ["repo_id","patch"]),
            "patch_revert": (self.fs.revert_patch, ["patch_id"]),
            "terminal_exec": (self.terminal.exec, ["repo_id","argv","command","timeout","env"]),
            "terminal_start": (self.terminal.start, ["repo_id","command"]),
            "terminal_write": (self.terminal.write, ["session_id","data"]),
            "terminal_read": (self.terminal.read, ["session_id","clear"]),
            "terminal_kill": (self.terminal.kill, ["session_id"]),
            "git_status": (self.git.status, ["repo_id"]),
            "git_diff": (self.git.diff, ["repo_id","staged","path"]),
            "git_log": (self.git.log, ["repo_id","limit"]),
            "git_show": (self.git.show, ["repo_id","ref","path"]),
            "git_branches": (self.git.branches, ["repo_id"]),
            "git_checkout": (self.git.checkout, ["repo_id","branch","create"]),
            "git_add": (self.git.add, ["repo_id","paths"]),
            "git_commit": (self.git.commit, ["repo_id","message"]),
            "git_restore": (self.git.restore, ["repo_id","paths","staged"]),
            "git_push": (self.git.push, ["repo_id","remote","branch"]),
            "task_list": (self.tasks.list, ["repo_id"]),
            "task_run": (self.tasks.run, ["repo_id","name","timeout"]),
            "code_symbols": (self.code.symbols, ["repo_id","path"]),
            "code_references": (self.code.references, ["repo_id","symbol","max_results"]),
            "code_definition": (self.code.definition, ["repo_id","symbol"]),
            "code_hover": (self.code.hover, ["repo_id","path","line","character"]),
            "code_definition_at": (self.code.definition_at, ["repo_id","path","line","character"]),
            "code_references_at": (self.code.references_at, ["repo_id","path","line","character","include_declaration"]),
            "code_diagnostics": (self.code.diagnostics, ["repo_id","path","wait_ms"]),
            "code_workspace_symbols": (self.code.workspace_symbols, ["repo_id","query","max_results"]),
            "code_call_hierarchy": (self.code.call_hierarchy, ["repo_id","path","line","character","direction"]),
            "skill_list": (self.skills.list, ["repo_id"]),
            "skill_read": (self.skills.read, ["repo_id","name"]),
            "skill_search": (self.skills.search, ["repo_id","query"]),
            "agent_list": (self.agents.list, []),
            "agent_delegate": (self.agents.delegate, ["repo_id","agent","task","mode","timeout"]),
            "mcp_list": (self.mcp.list_servers, []),
            "mcp_tools": (self.mcp.list_tools, ["server_id"]),
            "mcp_call": (self.mcp.call, ["repo_id","server_id","tool_name","arguments"]),
        }
        if name == "process_list":
            return {"processes": self.terminal.list_sessions()}
        if name == "code_lsp_status":
            return self.code.lsp_status(actor)
        if name == "history_calls":
            return {"calls": self.storage.list_calls(int(a.get("limit",100)), a.get("repo_id"))}
        if name == "history_patches":
            return {"patches": self.storage.list_patches(int(a.get("limit",100)), a.get("repo_id"))}
        if name == "approval_list":
            status = a.get("status", "pending")
            return {"approvals": self.storage.list_approvals(None if status == "all" else status)}
        if name not in mapping:
            raise KeyError(f"unknown tool: {name}")
        fn, accepted = mapping[name]
        filtered = {k: v for k, v in kwargs.items() if k in accepted and v is not None}
        self.ctx.clear_last_call()
        result = fn(actor, **filtered)
        if inspect.isawaitable(result):
            result = await result
        call_id = self.ctx.last_call_id()
        if call_id is not None:
            self.storage.set_call_result(call_id, result)
        return result
