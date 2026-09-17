# DevMesh Studio 1.1 — verification report

## Automated regression suite

Release-candidate result in the project virtual environment: **44 passed, 0 failed, 0 skipped**.

The suite includes the Qt offscreen desktop smoke test, MCP/OAuth integration tests, runtime/process tests, and the installer lifecycle regression tests added for the cross-platform distribution flow.

Verified paths include:

- SQLite schema, settings, repository registration/removal and repository statistics
- default pattern-based `allow / ask / deny` permission model
- one-time approvals, persistent approvals and approval consumption
- repository root confinement and `..`/absolute traversal rejection
- secret-like `.env` read approval behavior
- filesystem read, read-many, stat, list, tree, glob, grep and write
- exact text edits with unified patch-history recording
- patch preview, validation, application and reverse/revert
- Git status, diff, log, show, branches, checkout/create, add, commit and restore
- default remote denial of Git push
- shell-free terminal argv execution and explicit shell pipeline execution
- hard denial of destructive terminal patterns
- persistent PTY start/read/write/kill/list behavior
- project-task detection and actual pytest task execution
- Python AST symbol discovery plus definition/reference lookup
- real stdio LSP JSON-RPC framing, initialize/initialized lifecycle, persistent server process management and clean shutdown
- LSP document synchronization plus document symbols, hover, semantic definitions, semantic references, pull/push diagnostics, workspace symbols and incoming call hierarchy using a deterministic fake language server
- auto-detection/custom configuration model for Pyright/BasedPyright/Pylsp/Jedi, TypeScript Language Server, rust-analyzer, gopls, clangd and JDTLS
- repository/global `SKILL.md` discovery, read and search
- Codex delegate path with a fake executable and fixed argv/cwd confinement
- downstream MCP server persistence, tool listing/call routing and exact allowlist denial
- OAuth dynamic client registration, PKCE authorization, token exchange and refresh-capable persistence
- credential rotation revokes refresh/authorization state and rotates the JWT signing secret while preserving registered OAuth clients for re-authorization
- authenticated MCP legacy `initialize` + tool discovery with **55 unique tools**
- modern MCP **2026-07-28** `server/discover`, `resultType`, protocol metadata, tool listing and tool invocation
- end-to-end MCP `fs_read`
- end-to-end remote `fs_edit` approval request → local approval → retry → verified file mutation
- actual DevMesh server subprocess startup and `/healthz`
- runtime supervisor start/stop behavior
- Cloudflare Quick Tunnel URL extraction
- fake-cloudflared end-to-end supervisor flow: gateway start → tunnel URL → DB public URL update → MCP URL → clean stop
- history tools, patch history, approval listing and process listing
- tool registry uniqueness and read/destructive/open-world annotation checks
- Linux installer launcher routing for run/upgrade/config/paths/doctor/uninstall
- uninstall preserves user state by default; purge removes config/data/cache and invokes keyring cleanup
- install/config/data/cache environment-path overrides
- frozen runtime gateway selection for packaged builds
- Windows PyInstaller plan: separate `DevMesh Studio.exe` GUI and sibling `devmesh-server.exe` gateway

## Static verification

- every Python source module compiles with `compileall`
- `devmesh.py` and `install_desktop.py` compile successfully
- the PyQt page/controller/dialog modules compile successfully
- MCP schemas are generated from one registry and validated by tests
- package metadata parses and the source tree can be imported without GUI dependencies for server/core use
- a wheel is built from the source with `pip --no-deps --no-build-isolation`, installed into a clean target directory, and core/runtime/tool/supervisor modules import successfully

## Qt desktop verification

`tests/test_ui_smoke.py` launches the real `MainWindow` using Qt's offscreen platform, with credentials pre-seeded so the first-run dialog does not block. It checks that all 13 control-center pages are constructed and then shuts the window down cleanly.

The project `.venv` contains PyQt6 and the offscreen smoke test is executed as part of the 44-test release-candidate suite. For end users, the platform installers create or package the required runtime automatically; `python3 devmesh.py` remains the source-development launcher.

## External integration limits

No claim is made that a real installed Codex, Claude Code or OpenCode binary was invoked in this container. The agent-delegation mechanism itself is regression-tested with a fake executable so argument templating, shell avoidance, working-directory confinement, timeout/result capture and audit behavior are exercised.

Likewise, a real arbitrary third-party MCP provider was not contacted. The downstream adapter and allowlist behavior are tested with a fake MCP adapter; live stdio/Streamable-HTTP providers are intentionally environment-dependent.

The Cloudflare supervisor is tested with a fake `cloudflared` process so creation/parsing/state transitions are deterministic. The production code downloads/runs the official cloudflared binary on the target system.

## Known warning

Python 3.14 emits a deprecation warning for `pty.forkpty()` from a multi-threaded process during the PTY test. Starlette's test client also emits an AnyIO alias deprecation warning. Neither warning fails the suite. The current PTY implementation remains functional, but a future release should move long-lived PTY spawning to a helper process before this becomes a runtime compatibility issue.
