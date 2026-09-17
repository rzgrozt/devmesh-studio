from __future__ import annotations

import subprocess
from pathlib import Path
import pytest

from devmesh_studio.core.storage import Storage


@pytest.fixture
def repo_env(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "devmesh@example.test"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "DevMesh Test"], cwd=root, check=True)
    (root / "app.py").write_text("def greet(name):\n    return f'hello {name}'\n\nprint(greet('world'))\n", encoding="utf-8")
    (root / "README.md").write_text("# Test Repo\n\nhello devmesh\n", encoding="utf-8")
    (root / "pyproject.toml").write_text("[tool.pytest.ini_options]\ntestpaths=['tests']\n", encoding="utf-8")
    (root / "tests").mkdir()
    (root / "tests" / "test_basic.py").write_text("def test_ok():\n    assert 2 + 2 == 4\n", encoding="utf-8")
    skill = root / ".codex" / "skills" / "demo"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: demo-skill\ndescription: Demo skill for tests\n---\n# Demo\nDo the thing.\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=root, check=True)

    db = tmp_path / "devmesh.db"
    storage = Storage(db)
    repo = storage.add_repository(root)
    return storage, repo, root
