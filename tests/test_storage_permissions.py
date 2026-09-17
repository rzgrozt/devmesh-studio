from __future__ import annotations

from devmesh_studio.core.permissions import PermissionEngine


def test_default_permission_model(repo_env):
    storage, repo, root = repo_env
    p = PermissionEngine(storage)
    assert p.decide("chatgpt", repo["id"], "read", "README.md", {}).effect == "allow"
    assert p.decide("chatgpt", repo["id"], "edit", "README.md", {}).effect == "allow"
    assert p.decide("chatgpt", repo["id"], "bash", "python custom_script.py", {}).effect == "ask"
    assert p.decide("chatgpt", repo["id"], "git_push", "git push origin", {}).effect == "deny"
    assert p.decide("desktop", repo["id"], "git_push", "git push origin", {}).effect == "allow"


def test_approval_always_creates_rule(repo_env):
    storage, repo, root = repo_env
    p = PermissionEngine(storage)
    d = p.decide(
        "chatgpt",
        repo["id"],
        "git_write",
        "git checkout feature/demo",
        {"branch": "feature/demo"},
        suggested_pattern="git checkout *",
    )
    assert d.effect == "ask" and d.approval_id
    storage.resolve_approval(d.approval_id, "approved_always")
    assert p.decide(
        "chatgpt",
        repo["id"],
        "git_write",
        "git checkout feature/other",
        {"branch": "feature/other"},
    ).effect == "allow"
