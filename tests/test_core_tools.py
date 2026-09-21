from __future__ import annotations

import asyncio
import os
import subprocess
import time
from pathlib import Path

import pytest

from devmesh_studio.core.permissions import ApprovalRequired, PermissionEngine
from devmesh_studio.tools.base import ToolContext
from devmesh_studio.tools.filesystem import FilesystemTools
from devmesh_studio.tools.git import GitTools
from devmesh_studio.tools.terminal import TerminalTools
from devmesh_studio.tools.tasks import TaskTools
from devmesh_studio.tools.code_intel import CodeIntelTools
from devmesh_studio.tools.skills import SkillTools
from devmesh_studio.tools.agents import AgentTools


def test_files_read_search_tree_and_path_confinement(repo_env):
    storage, repo, root = repo_env
    fs = FilesystemTools(ToolContext(storage))
    r = fs.read("chatgpt", repo["id"], "app.py")
    assert "def greet" in r["text"]
    assert fs.grep("chatgpt", repo["id"], "hello")["results"]
    assert any(i["path"] == "app.py" for i in fs.tree("chatgpt", repo["id"])["items"])
    with pytest.raises(PermissionError):
        fs.read("chatgpt", repo["id"], "../outside.txt")


def test_secret_like_read_requires_approval(repo_env):
    storage, repo, root = repo_env
    (root / ".env").write_text("TOKEN=secret\n")
    fs = FilesystemTools(ToolContext(storage))
    with pytest.raises(ApprovalRequired) as e:
        fs.read("chatgpt", repo["id"], ".env")
    approval = e.value.approval_id
    storage.resolve_approval(approval, "approved_once")
    assert "TOKEN=secret" in fs.read("chatgpt", repo["id"], ".env")["text"]


def test_remote_edit_is_default_allowed_and_records_patch_history(repo_env):
    storage, repo, root = repo_env
    fs = FilesystemTools(ToolContext(storage))
    result = fs.edit("chatgpt", repo["id"], "README.md", "hello devmesh", "hello chatgpt")
    assert result["patch_id"]
    assert "hello chatgpt" in (root / "README.md").read_text()
    patches = storage.list_patches()
    assert patches and "README.md" in patches[0]["changed_paths"]


def test_apply_and_revert_git_patch(repo_env):
    storage, repo, root = repo_env
    fs = FilesystemTools(ToolContext(storage))
    original = (root / "README.md").read_text()
    (root / "README.md").write_text(original + "extra line\n")
    patch = subprocess.run(["git", "diff", "--", "README.md"], cwd=root, capture_output=True, text=True, check=True).stdout
    subprocess.run(["git", "restore", "README.md"], cwd=root, check=True)
    preview = fs.patch_preview("desktop", repo["id"], patch)
    assert preview["valid"]
    assert preview["diff"] == patch
    applied = fs.apply_patch("desktop", repo["id"], patch)
    assert applied["diff"] == patch
    assert "extra line" in (root / "README.md").read_text()
    reverted = fs.revert_patch("desktop", applied["patch_id"])
    assert "-extra line" in reverted["diff"]
    assert (root / "README.md").read_text() == original


def test_git_read_write_operations(repo_env):
    storage, repo, root = repo_env
    git = GitTools(ToolContext(storage))
    assert git.status("chatgpt", repo["id"])["returncode"] == 0
    (root / "new.txt").write_text("x")
    git.add("desktop", repo["id"], ["new.txt"])
    result = git.commit("desktop", repo["id"], "add new")
    assert result["returncode"] == 0
    assert "add new" in git.log("chatgpt", repo["id"], 3)["stdout"]


def test_terminal_exec_and_hard_deny(repo_env):
    storage, repo, root = repo_env
    term = TerminalTools(ToolContext(storage))
    result = term.exec("desktop", repo["id"], argv=["python3", "-c", "print(21*2)"])
    assert result["returncode"] == 0 and "42" in result["stdout"]
    result2 = term.exec("desktop", repo["id"], command="printf 'a\\nb\\n' | tail -n 1")
    assert result2["stdout"].strip() == "b"
    with pytest.raises(PermissionError):
        term.exec("desktop", repo["id"], command="rm -rf /")


def test_safe_terminal_git_classification():
    from devmesh_studio.tools.terminal import _is_safe_developer_command

    assert _is_safe_developer_command(["git", "add", "src/app.py"], "git add src/app.py")
    assert _is_safe_developer_command(["git", "commit", "-m", "save"], "git commit -m save")
    assert _is_safe_developer_command(["git", "switch", "feature"], "git switch feature")
    assert _is_safe_developer_command(["git", "restore", "--staged", "app.py"], "git restore --staged app.py")
    assert not _is_safe_developer_command(["git", "checkout", "--", "app.py"], "git checkout -- app.py")
    assert not _is_safe_developer_command(["git", "branch", "-D", "feature"], "git branch -D feature")
    assert not _is_safe_developer_command(["git", "push", "--force"], "git push --force")


def test_persistent_pty_session(repo_env):
    storage, repo, root = repo_env
    term = TerminalTools(ToolContext(storage))
    s = term.start("desktop", repo["id"], "printf 'PTY_OK\\n'")
    for _ in range(20):
        time.sleep(0.05)
        out = term.read("desktop", s["session_id"], clear=False)
        if "PTY_OK" in out["text"]:
            break
    assert "PTY_OK" in out["text"]


def test_tasks_code_intel_and_skills(repo_env):
    storage, repo, root = repo_env
    ctx = ToolContext(storage)
    tasks = TaskTools(ctx)
    assert "test" in tasks.list("desktop", repo["id"])["tasks"]
    result = tasks.run("desktop", repo["id"], "test", timeout=120)
    assert result["returncode"] == 0
    code = CodeIntelTools(ctx)
    symbols = code.symbols("chatgpt", repo["id"], "app.py")["symbols"]
    assert any(x["name"] == "greet" for x in symbols)
    defs = code.definition("chatgpt", repo["id"], "greet")["definitions"]
    assert defs and defs[0]["path"] == "app.py"
    skills = SkillTools(ctx)
    names = [x["name"] for x in skills.list("chatgpt", repo["id"])["skills"]]
    assert "demo-skill" in names
    assert "Do the thing" in skills.read("chatgpt", repo["id"], "demo-skill")["text"]


def test_agent_delegate_with_fake_codex(repo_env, tmp_path, monkeypatch):
    storage, repo, root = repo_env
    bindir = tmp_path / "bin"; bindir.mkdir()
    fake = bindir / "codex"
    fake.write_text("#!/bin/sh\nprintf 'FAKE_CODEX %s\\n' \"$*\"\n")
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", str(bindir) + os.pathsep + os.environ.get("PATH", ""))
    agents = AgentTools(ToolContext(storage))
    listed = {a["id"]: a for a in agents.list("desktop")["agents"]}
    assert listed["codex"]["available"]
    result = asyncio.run(agents.delegate("desktop", repo["id"], "codex", "review this", "propose", 30))
    assert result["returncode"] == 0 and "review this" in result["stdout"]


def test_remaining_filesystem_operations(repo_env):
    storage, repo, root = repo_env
    fs = FilesystemTools(ToolContext(storage))
    many = fs.read_many("chatgpt", repo["id"], ["app.py", "README.md"])
    assert len(many["files"]) == 2
    assert fs.stat("chatgpt", repo["id"], "app.py")["is_file"] is True
    listing = fs.list_dir("chatgpt", repo["id"], ".")
    assert any(x["name"] == "app.py" for x in listing["entries"])
    globbed = fs.glob("chatgpt", repo["id"], "**/*.py")
    assert any(x["path"] == "app.py" for x in globbed["matches"])
    written = fs.write("desktop", repo["id"], "generated/note.txt", "created by devmesh\n")
    assert written["patch_id"] and (root / "generated/note.txt").read_text() == "created by devmesh\n"


def test_remaining_git_operations(repo_env):
    storage, repo, root = repo_env
    git = GitTools(ToolContext(storage))
    original_branch = subprocess.run(["git", "branch", "--show-current"], cwd=root, capture_output=True, text=True, check=True).stdout.strip()
    assert "initial" in git.show("chatgpt", repo["id"], "HEAD")["stdout"] or "app.py" in git.show("chatgpt", repo["id"], "HEAD")["stdout"]
    assert original_branch in git.branches("chatgpt", repo["id"])["stdout"]
    assert git.checkout("desktop", repo["id"], "devmesh-test-branch", create=True)["returncode"] == 0
    assert subprocess.run(["git", "branch", "--show-current"], cwd=root, capture_output=True, text=True).stdout.strip() == "devmesh-test-branch"
    git.checkout("desktop", repo["id"], original_branch)
    (root / "README.md").write_text("changed locally\n")
    git.restore("desktop", repo["id"], ["README.md"])
    assert "Test Repo" in (root / "README.md").read_text()
    with pytest.raises(PermissionError):
        git.push("chatgpt", repo["id"], "origin")


def test_terminal_write_read_kill_and_process_list(repo_env):
    storage, repo, root = repo_env
    term = TerminalTools(ToolContext(storage))
    s = term.start("desktop", repo["id"], "cat")
    term.write("desktop", s["session_id"], "TERMINAL_ECHO\n")
    out = {"text": ""}
    for _ in range(30):
        time.sleep(0.05)
        out = term.read("desktop", s["session_id"], clear=False)
        if "TERMINAL_ECHO" in out["text"]:
            break
    assert "TERMINAL_ECHO" in out["text"]
    assert any(x["session_id"] == s["session_id"] for x in term.list_sessions())
    assert term.kill("desktop", s["session_id"])["killed"] is True


def test_code_references_and_skill_search(repo_env):
    storage, repo, root = repo_env
    ctx = ToolContext(storage)
    code = CodeIntelTools(ctx)
    refs = code.references("chatgpt", repo["id"], "greet")["results"]
    assert len(refs) >= 2
    skills = SkillTools(ctx)
    found = skills.search("chatgpt", repo["id"], "demo")["skills"]
    assert any(x["name"] == "demo-skill" for x in found)
