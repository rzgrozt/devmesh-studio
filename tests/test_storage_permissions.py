from __future__ import annotations

from devmesh_studio.core.permissions import PermissionEngine


def test_default_permission_model(repo_env):
    storage, repo, root = repo_env
    p = PermissionEngine(storage)
    assert p.decide("chatgpt", repo["id"], "read", "README.md", {}).effect == "allow"
    assert p.decide("chatgpt", repo["id"], "edit", "README.md", {}).effect == "allow"
    assert p.decide("chatgpt", repo["id"], "bash", "python custom_script.py", {}).effect == "ask"
    assert p.decide("chatgpt", repo["id"], "git_stage", "git add", {}).effect == "allow"
    assert p.decide("chatgpt", repo["id"], "git_commit", "git commit", {}).effect == "allow"
    assert p.decide("chatgpt", repo["id"], "git_checkout", "git checkout main", {}).effect == "allow"
    assert p.decide("chatgpt", repo["id"], "git_restore", "git restore file", {}).effect == "ask"
    assert p.decide("chatgpt", repo["id"], "git_push", "git push origin", {}).effect == "ask"
    assert p.decide("chatgpt", repo["id"], "git_force_push", "git push --force", {}).effect == "deny"
    assert p.decide("desktop", repo["id"], "git_push", "git push origin", {}).effect == "allow"


def test_approval_always_creates_rule(repo_env):
    storage, repo, root = repo_env
    p = PermissionEngine(storage)
    d = p.decide(
        "chatgpt",
        repo["id"],
        "git_restore",
        "git checkout feature/demo",
        {"branch": "feature/demo"},
        suggested_pattern="git checkout *",
    )
    assert d.effect == "ask" and d.approval_id
    storage.resolve_approval(d.approval_id, "approved_always")
    assert p.decide(
        "chatgpt",
        repo["id"],
        "git_restore",
        "git checkout feature/other",
        {"branch": "feature/other"},
    ).effect == "allow"


def test_v3_migration_updates_git_defaults_without_resetting_user_rules(repo_env):
    storage, repo, root = repo_env
    storage.upsert_permission(repo["id"], "edit", "*", "ask", 0)
    storage.upsert_permission(repo["id"], "git_write", "*", "ask", 0)
    storage.upsert_permission(repo["id"], "git_push", "*", "deny", 0)
    storage.set_setting("permission_profile_version", "2")
    storage._upgrade_permission_profile()
    rules = {(r["action"], r["pattern"]): r["effect"] for r in storage.list_permissions(repo["id"])}
    assert rules[("edit", "*")] == "ask"
    assert rules[("git_write", "*")] == "allow"
    assert rules[("git_push", "*")] == "ask"
    assert rules[("git_force_push", "*")] == "deny"
