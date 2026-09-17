# Architecture notes

## Design principle

DevMesh Studio is a local execution/control plane. It intentionally does not require Codex, Claude Code, OpenCode, or another model for normal coding operations. ChatGPT can directly compose narrow MCP tools. Coding-agent CLIs are optional delegates for independent review or larger agent-native tasks.

## Components

- `core/storage.py` — SQLite schema/state, audit history, approvals, permissions, repositories, MCP configurations, OAuth state.
- `core/repository.py` — repository root resolution and path confinement.
- `core/permissions.py` — ordered pattern-based allow/ask/deny evaluation.
- `runtime/auth.py` — OAuth 2 authorization-code + PKCE and refresh tokens.
- `runtime/mcp_http.py` — authenticated HTTP JSON-RPC MCP endpoint used by ChatGPT; supports MCP 2026-07-28 stateless discovery/results plus the legacy initialize handshake for older clients.
- `runtime/tool_registry.py` — schemas and normalized dispatcher for the 55 tools.
- `tools/*` — filesystem, patching, Git, PTY, project tasks, code intelligence, skills, agents, downstream MCP.
- `services/runtime_supervisor.py` — local server and zero-account Quick Tunnel lifecycle.
- `ui/*` — PyQt6 desktop control plane.

## Permission semantics

Rules have `(repository, action, pattern, effect, priority)`. Matching rules are ordered by priority/id and the highest one wins. `effect` is `allow`, `ask`, or `deny`. A pending `ask` is persisted; the UI can approve once, approve always (materializing a persistent allow rule), or deny.

## Sandboxing boundary

Filesystem tools are strictly repository-root confined. Terminal subprocesses still execute with the host user's OS authority; command permission rules and hard-deny patterns reduce risk but are not an OS sandbox. For untrusted code or high-risk automation, run DevMesh inside a disposable VM/container/user account.

- `services/lsp.py` — persistent stdio LSP process pool, protocol framing, server discovery, document synchronization and semantic result normalization.
