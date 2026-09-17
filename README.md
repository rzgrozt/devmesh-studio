# DevMesh Studio 1.1

DevMesh Studio turns a normal ChatGPT conversation into a local coding environment without making another model the mandatory middleman.

**ChatGPT reasons. DevMesh operates the machine.**

DevMesh exposes a broad coding runtime over authenticated MCP while a PyQt6 desktop application manages repositories, permissions, OAuth, the HTTPS tunnel, Git state, patch history, terminal activity, downstream MCP servers, skills, coding-agent delegates, approvals, and audit statistics.

## Fast start

Requirements: Python 3.11+, Git, and internet access. Tailscale is recommended for the default stable public MCP URL; Cloudflare Quick Tunnel remains available as a temporary fallback.

### Linux

Install for the current user with one command:

```bash
curl -fsSL https://raw.githubusercontent.com/rzgrozt/devmesh-studio/main/install.sh | bash
```

The installer places the application under `~/.local/opt/devmesh-studio`, creates an isolated virtual environment, installs the `devmesh` launcher under `~/.local/bin`, and creates the application-menu entry automatically. A local source checkout can be installed with `bash install.sh` instead.

After installation:

```bash
devmesh                    # open DevMesh Studio
devmesh doctor             # check the local install
devmesh paths              # show app/config/data/cache locations
devmesh config             # show configuration/state locations
devmesh config --open      # open the config directory
devmesh upgrade            # git pull + refresh the isolated runtime
devmesh uninstall          # remove the app, preserve user data
devmesh uninstall --purge  # remove app + config + database + cache
```

`uninstall` deliberately preserves repositories, permissions, audit history, credentials metadata and other DevMesh state. Use `--purge` only when you want a completely clean removal; purge also removes DevMesh's JWT signing secret from the OS keyring when that backend is available.

The one-line installer defaults to `https://github.com/rzgrozt/devmesh-studio.git`. Before publishing under a different repository path, set `DEVMESH_REPO_URL` or update the installer constant.

### Windows

The Windows installer detects Windows and builds native executables with PyInstaller instead of leaving DevMesh as a Python-script launcher:

```powershell
irm https://raw.githubusercontent.com/rzgrozt/devmesh-studio/main/install.ps1 | iex
```

It builds and installs both `DevMesh Studio.exe` and the sibling `devmesh-server.exe` gateway under `%LOCALAPPDATA%\Programs\DevMesh Studio`, then creates Start Menu and Desktop shortcuts. The separate gateway executable is required because a frozen GUI executable cannot launch the MCP runtime with Python's `-m` mechanism.

From a source checkout you can build without installing:

```powershell
.\install.ps1 -Action build
```

Or install/update/remove explicitly:

```powershell
.\install.ps1 -Action install
.\install.ps1 -Action upgrade
.\install.ps1 -Action uninstall
.\install.ps1 -Action purge
```

### Development launcher

`python3 devmesh.py` remains available for source-development runs. It creates a repository-local `.venv` when needed; normal end-user installs should use the platform installer above.

## First launch

1. Choose your DevMesh OAuth username and password. The password itself is never stored; DevMesh stores an Argon2 verifier.
2. Open **Repositories** and add every codebase ChatGPT may access. Each repository becomes a separate sandbox root.
3. Install/sign in to Tailscale once, enable Funnel for the device/tailnet, then open **Connections** and click **Start**. DevMesh starts the local FastAPI MCP runtime and publishes it through **Tailscale Funnel** in background mode by default.
4. Copy the **ChatGPT MCP URL** shown in the application. It uses this machine's stable `*.ts.net` hostname, for example `https://devmesh.my-tailnet.ts.net/mcp`.
5. In ChatGPT's custom MCP/app setup, register that URL, choose OAuth/authenticated access, scan tools, and authorize with the credentials you created in DevMesh Studio.

The Tailscale Funnel hostname is stable across DevMesh and machine restarts, so ChatGPT normally needs to be configured only once. **Cloudflare Quick Tunnel** remains available in Connections as an explicit temporary fallback when Tailscale is unavailable; its `trycloudflare.com` hostname is ephemeral.

## Desktop control center

- **Dashboard** — runtime status, public MCP URL, repository/call/patch/approval counters, live activity.
- **Repositories** — add/remove/switch codebases; branch, HEAD, Git status, recent activity.
- **Live Calls** — every ChatGPT/desktop tool invocation and duration.
- **Changes** — patch timeline, unified diff viewer, explicit local revert.
- **Approvals** — approve once, always allow matching, or deny pending ChatGPT actions.
- **Permissions** — per-repository `allow / ask / deny` rules with patterns and priorities.
- **Terminal** — explicitly run commands locally and inspect command history.
- **Git** — inspect status, working/index diffs, log, and branches.
- **MCP Servers** — add/edit/remove downstream stdio or HTTP MCP servers, test tool discovery, and define the exact remote-invocation allowlist.
- **Language Servers** — inspect auto-detected LSP servers and configure custom stdio language-server commands without editing files.
- **Agents** — discover Codex, Claude Code, and OpenCode CLI delegates.
- **Skills** — discover `SKILL.md` files from repository/global Codex, Claude, OpenCode, and `.agents` skill locations.
- **Connections** — credentials, local port, automatic tunnel, runtime controls, public URL and logs.

## ChatGPT coding tools

DevMesh currently exposes **55 MCP tools**. The HTTP endpoint supports the current MCP **2026-07-28** stateless `server/discover` path and retains the legacy initialize handshake for older clients.

### Repository and filesystem
`repo_list`, `repo_info`, `repo_stats`, `repo_recent_changes`, `fs_read`, `fs_read_many`, `fs_stat`, `fs_list`, `fs_tree`, `fs_glob`, `fs_grep`, `fs_write`, `fs_edit`, `patch_preview`, `patch_apply`, `patch_revert`

### Terminal and processes
`terminal_exec`, `terminal_start`, `terminal_write`, `terminal_read`, `terminal_kill`, `process_list`

### Git
`git_status`, `git_diff`, `git_log`, `git_show`, `git_branches`, `git_checkout`, `git_add`, `git_commit`, `git_restore`, `git_push`

### Project tasks and code intelligence
`task_list`, `task_run`, `code_symbols`, `code_references`, `code_definition`, `code_hover`, `code_definition_at`, `code_references_at`, `code_diagnostics`, `code_workspace_symbols`, `code_call_hierarchy`, `code_lsp_status`

`task_list` auto-detects common Python, npm, Cargo, Go and Make tasks. DevMesh now has a real persistent stdio LSP client. It auto-detects common language servers including BasedPyright/Pyright/Pylsp/Jedi (Python), TypeScript Language Server, rust-analyzer, gopls, clangd and JDTLS. Semantic tools use LSP when available; `code_symbols`, `code_definition` and `code_references` retain local fallbacks when no server is installed. Position-aware LSP tools use **1-based lines and 0-based characters**.

Open **Language Servers** in the desktop app to see what DevMesh detected. Custom servers can be configured there with JSON such as:

```json
{
  "python": {
    "command": ["pyright-langserver", "--stdio"],
    "extensions": [".py", ".pyi"]
  }
}
```

Language servers themselves are external developer tools and are not bundled into DevMesh; if a server is unavailable, DevMesh reports that status and uses its safe local fallback where one exists.

### Skills, agents, downstream MCP and history
`skill_list`, `skill_read`, `skill_search`, `agent_list`, `agent_delegate`, `mcp_list`, `mcp_tools`, `mcp_call`, `history_calls`, `history_patches`, `approval_list`

## Safety model

Every filesystem path is resolved against an explicitly registered repository root. `..` traversal and absolute paths outside the repository are rejected.

Default remote permissions are conservative:

- reading/search/list/Git inspection/code intelligence/skills: **allow**
- `.env` and secret-like reads: **ask**
- edits/patches/terminal/Git writes/tasks/agent delegation/downstream MCP calls: **ask**
- `git push`: **deny**
- external-directory access: **deny**
- obviously destructive command patterns such as `rm -rf /`, `mkfs`, raw-disk writes and `sudo *`: hard denied before the configurable permission layer

A remote `ask` returns an approval ID to ChatGPT. Approve it in **Approvals**, then retry the same call. **Approve always** adds a persistent matching permission rule.

Actions clicked directly in the local PyQt application count as explicit human actions and do not generate a second approval prompt.

Downstream MCP discovery and invocation are deliberately separate: adding a server does not remotely expose every tool. Exact tool names must be in that server's `allowed_tools` list.

## Data and secrets

On Linux, state is stored under `${XDG_DATA_HOME:-~/.local/share}/devmesh-studio/`, configuration metadata under `${XDG_CONFIG_HOME:-~/.config}/devmesh-studio/`, and disposable cache under `${XDG_CACHE_HOME:-~/.cache}/devmesh-studio/`. On Windows, DevMesh uses `%LOCALAPPDATA%\DevMesh Studio\Data`, `%APPDATA%\DevMesh Studio`, and `%LOCALAPPDATA%\DevMesh Studio\Cache`. Override these for development/tests with `DEVMESH_DATA_DIR`, `DEVMESH_CONFIG_DIR`, and `DEVMESH_CACHE_DIR`.

- repository configuration, audit metadata, permissions and patches: SQLite
- passwords: never stored; only Argon2 hashes
- JWT signing secret: OS keyring when available, otherwise a user-only `0600` fallback file
- downstream MCP environment values stay local and are not returned through `mcp_list`

## Architecture

```text
ChatGPT
  │ OAuth + MCP
  ▼
DevMesh FastAPI runtime ───── filesystem / Git / PTY / tasks / skills
  │                                      │
  │ shared SQLite audit/event store      ├── Repository A
  │                                      ├── Repository B
  ▼                                      └── Repository C
DevMesh Studio (PyQt6)
  ├── runtime/tunnel supervisor
  ├── approval + permission UI
  ├── patch/call/terminal monitoring
  ├── downstream MCP management
  └── optional Codex/Claude/OpenCode delegates
```

## Testing

The source archive includes the complete pytest suite. Run:

```bash
python -m pytest -q
```

See `TEST_REPORT.md` for the test matrix used for this release.

## License

GPL-3.0-or-later. PyQt6's community edition is GPL; this project uses a compatible license. If distributing DevMesh under different terms, review Riverbank's commercial PyQt licensing options.
