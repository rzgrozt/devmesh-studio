from __future__ import annotations

import difflib
import fnmatch
import glob as globlib
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from .base import ToolContext


SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".mypy_cache", ".pytest_cache", "dist", "build"}


class FilesystemTools:
    def __init__(self, ctx: ToolContext):
        self.ctx = ctx

    def _read_permission(self, actor: str, repo_id: int, path: Path, args: dict[str, Any]) -> None:
        rel = self.ctx.repos.relative(repo_id, path)
        resource = rel or "."
        self.ctx.permissions.require(actor, repo_id, "read", resource, args, suggested_pattern=resource)

    def read(self, actor: str, repo_id: int, path: str, start_line: int = 1, end_line: int | None = None, max_chars: int = 120_000) -> dict[str, Any]:
        args = {"path": path, "start_line": start_line, "end_line": end_line}
        with self.ctx.record(actor, repo_id, "fs_read", path, args):
            target = self.ctx.repos.resolve(repo_id, path, must_exist=True)
            if not target.is_file():
                raise IsADirectoryError(path)
            self._read_permission(actor, repo_id, target, args)
            raw = target.read_bytes()
            if b"\x00" in raw[:4096]:
                return {"path": self.ctx.repos.relative(repo_id, target), "binary": True, "size": len(raw)}
            text = raw.decode("utf-8", errors="replace")
            lines = text.splitlines()
            start = max(1, int(start_line))
            end = min(len(lines), int(end_line) if end_line else len(lines))
            selected = "\n".join(lines[start - 1 : end])
            truncated = len(selected) > max_chars
            if truncated:
                selected = selected[:max_chars]
            return {
                "path": self.ctx.repos.relative(repo_id, target),
                "start_line": start,
                "end_line": end,
                "total_lines": len(lines),
                "text": selected,
                "truncated": truncated,
            }

    def read_many(self, actor: str, repo_id: int, paths: list[str], max_chars_each: int = 50_000) -> dict[str, Any]:
        if len(paths) > 50:
            raise ValueError("maximum 50 files per call")
        return {"files": [self.read(actor, repo_id, p, 1, None, max_chars_each) for p in paths]}

    def stat(self, actor: str, repo_id: int, path: str) -> dict[str, Any]:
        args = {"path": path}
        with self.ctx.record(actor, repo_id, "fs_stat", path, args):
            target = self.ctx.repos.resolve(repo_id, path, must_exist=True)
            self._read_permission(actor, repo_id, target, args)
            s = target.stat()
            return {
                "path": self.ctx.repos.relative(repo_id, target),
                "is_file": target.is_file(),
                "is_dir": target.is_dir(),
                "size": s.st_size,
                "mtime": int(s.st_mtime),
            }

    def list_dir(self, actor: str, repo_id: int, path: str = ".", offset: int = 0, limit: int = 300) -> dict[str, Any]:
        args = {"path": path, "offset": offset, "limit": limit}
        with self.ctx.record(actor, repo_id, "fs_list", path, args):
            target = self.ctx.repos.resolve(repo_id, path, must_exist=True)
            if not target.is_dir():
                raise NotADirectoryError(path)
            rel = self.ctx.repos.relative(repo_id, target) or "."
            self.ctx.permissions.require(actor, repo_id, "list", rel, args, suggested_pattern=rel)
            entries = sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.casefold()))
            page = entries[offset : offset + min(limit, 1000)]
            return {
                "path": rel,
                "entries": [
                    {
                        "name": p.name,
                        "path": self.ctx.repos.relative(repo_id, p),
                        "type": "dir" if p.is_dir() else "file",
                        "size": p.stat().st_size if p.is_file() else None,
                    }
                    for p in page
                ],
                "next_offset": offset + len(page) if offset + len(page) < len(entries) else None,
            }

    def tree(self, actor: str, repo_id: int, path: str = ".", max_depth: int = 3, max_entries: int = 1000) -> dict[str, Any]:
        args = {"path": path, "max_depth": max_depth, "max_entries": max_entries}
        with self.ctx.record(actor, repo_id, "fs_tree", path, args):
            base = self.ctx.repos.resolve(repo_id, path, must_exist=True)
            if not base.is_dir():
                raise NotADirectoryError(path)
            rel = self.ctx.repos.relative(repo_id, base) or "."
            self.ctx.permissions.require(actor, repo_id, "list", rel, args, suggested_pattern=rel)
            root = self.ctx.repos.root(repo_id)
            items: list[dict[str, Any]] = []
            base_depth = len(base.relative_to(root).parts)
            for current, dirs, files in os.walk(base):
                cur = Path(current)
                depth = len(cur.relative_to(root).parts) - base_depth
                dirs[:] = [d for d in sorted(dirs) if d not in SKIP_DIRS]
                if depth >= max_depth:
                    dirs[:] = []
                for d in dirs:
                    items.append({"path": self.ctx.repos.relative(repo_id, cur / d), "type": "dir"})
                    if len(items) >= max_entries:
                        return {"root": rel, "items": items, "truncated": True}
                for f in sorted(files):
                    items.append({"path": self.ctx.repos.relative(repo_id, cur / f), "type": "file"})
                    if len(items) >= max_entries:
                        return {"root": rel, "items": items, "truncated": True}
            return {"root": rel, "items": items, "truncated": False}

    def glob(self, actor: str, repo_id: int, pattern: str, max_results: int = 500) -> dict[str, Any]:
        args = {"pattern": pattern, "max_results": max_results}
        with self.ctx.record(actor, repo_id, "fs_glob", pattern, args):
            self.ctx.permissions.require(actor, repo_id, "glob", pattern, args, suggested_pattern=pattern)
            root = self.ctx.repos.root(repo_id)
            matches = []
            for raw in globlib.iglob(str(root / pattern), recursive=True):
                p = Path(raw).resolve()
                try:
                    rel = str(p.relative_to(root))
                except ValueError:
                    continue
                if any(part in SKIP_DIRS for part in p.parts):
                    continue
                matches.append({"path": rel, "type": "dir" if p.is_dir() else "file"})
                if len(matches) >= max_results:
                    break
            return {"pattern": pattern, "matches": matches, "truncated": len(matches) >= max_results}

    def grep(self, actor: str, repo_id: int, query: str, glob: str | None = None, regex: bool = False, max_results: int = 200) -> dict[str, Any]:
        args = {"query": query, "glob": glob, "regex": regex, "max_results": max_results}
        with self.ctx.record(actor, repo_id, "fs_grep", query, args):
            self.ctx.permissions.require(actor, repo_id, "grep", query, args, suggested_pattern="*")
            root = self.ctx.repos.root(repo_id)
            rg = subprocess.run(["which", "rg"], capture_output=True, text=True)
            if rg.returncode == 0:
                cmd = ["rg", "--line-number", "--column", "--no-heading", "--color", "never"]
                if not regex:
                    cmd.append("--fixed-strings")
                if glob:
                    cmd += ["--glob", glob]
                cmd += [query, "."]
                proc = subprocess.run(cmd, cwd=root, capture_output=True, text=True, timeout=30)
                lines = proc.stdout.splitlines()[:max_results]
                results = []
                for line in lines:
                    parts = line.split(":", 3)
                    if len(parts) == 4:
                        results.append({"path": parts[0], "line": int(parts[1]), "column": int(parts[2]), "text": parts[3]})
                return {"results": results, "truncated": len(proc.stdout.splitlines()) > max_results}

            pattern = re.compile(query if regex else re.escape(query))
            results = []
            for p in root.rglob("*"):
                if not p.is_file() or any(part in SKIP_DIRS for part in p.parts):
                    continue
                rel = str(p.relative_to(root))
                if glob and not fnmatch.fnmatch(rel, glob):
                    continue
                try:
                    if p.stat().st_size > 2_000_000:
                        continue
                    for i, line in enumerate(p.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
                        m = pattern.search(line)
                        if m:
                            results.append({"path": rel, "line": i, "column": m.start() + 1, "text": line[:1000]})
                            if len(results) >= max_results:
                                return {"results": results, "truncated": True}
                except OSError:
                    continue
            return {"results": results, "truncated": False}

    def _snapshot_patch(self, repo_id: int, path: str, before: str, after: str) -> str:
        return "".join(
            difflib.unified_diff(
                before.splitlines(keepends=True),
                after.splitlines(keepends=True),
                fromfile=f"a/{path}",
                tofile=f"b/{path}",
            )
        )

    @staticmethod
    def _reverse_patch_for_display(patch: str) -> str:
        """Return a readable unified diff describing the reverse operation."""
        source = patch.splitlines()
        reversed_lines: list[str] = []
        index = 0
        while index < len(source):
            line = source[index]
            if line.startswith("--- ") and index + 1 < len(source) and source[index + 1].startswith("+++ "):
                reversed_lines.extend(("--- " + source[index + 1][4:], "+++ " + line[4:]))
                index += 2
                continue
            hunk = re.match(r"^@@ -(\d+(?:,\d+)?) \+(\d+(?:,\d+)?) @@(.*)$", line)
            if hunk:
                reversed_lines.append(f"@@ -{hunk.group(2)} +{hunk.group(1)} @@{hunk.group(3)}")
            elif line.startswith("-"):
                reversed_lines.append("+" + line[1:])
            elif line.startswith("+"):
                reversed_lines.append("-" + line[1:])
            else:
                reversed_lines.append(line)
            index += 1
        return "\n".join(reversed_lines)

    def write(self, actor: str, repo_id: int, path: str, content: str, overwrite: bool = True) -> dict[str, Any]:
        args = {"path": path, "content_sha256": self.ctx.storage.sha(content), "overwrite": overwrite}
        with self.ctx.record(actor, repo_id, "fs_write", path, args):
            target = self.ctx.repos.resolve(repo_id, path)
            rel = self.ctx.repos.relative(repo_id, target)
            self.ctx.permissions.require(actor, repo_id, "edit", rel, args, suggested_pattern=rel)
            if target.exists() and not overwrite:
                raise FileExistsError(path)
            before = target.read_text(encoding="utf-8", errors="replace") if target.exists() else ""
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            patch = self._snapshot_patch(repo_id, rel, before, content)
            patch_id = self.ctx.storage.add_patch(repo_id, actor, "fs_write", patch, [rel])
            return {"path": rel, "bytes": len(content.encode()), "patch_id": patch_id, "diff": patch}

    def edit(self, actor: str, repo_id: int, path: str, old: str, new: str, replace_all: bool = False) -> dict[str, Any]:
        args = {"path": path, "old_sha256": self.ctx.storage.sha(old), "new_sha256": self.ctx.storage.sha(new), "replace_all": replace_all}
        with self.ctx.record(actor, repo_id, "fs_edit", path, args):
            target = self.ctx.repos.resolve(repo_id, path, must_exist=True)
            rel = self.ctx.repos.relative(repo_id, target)
            self.ctx.permissions.require(actor, repo_id, "edit", rel, args, suggested_pattern=rel)
            before = target.read_text(encoding="utf-8")
            count = before.count(old)
            if count == 0:
                raise ValueError("old text was not found")
            if count > 1 and not replace_all:
                raise ValueError(f"old text occurs {count} times; set replace_all=true or provide a more specific match")
            after = before.replace(old, new) if replace_all else before.replace(old, new, 1)
            target.write_text(after, encoding="utf-8")
            patch = self._snapshot_patch(repo_id, rel, before, after)
            patch_id = self.ctx.storage.add_patch(repo_id, actor, "fs_edit", patch, [rel])
            return {"path": rel, "replacements": count if replace_all else 1, "patch_id": patch_id, "diff": patch}

    @staticmethod
    def _patch_paths(patch: str) -> list[str]:
        paths: list[str] = []
        for line in patch.splitlines():
            if line.startswith("+++ ") or line.startswith("--- "):
                raw = line[4:].split("\t", 1)[0]
                if raw == "/dev/null":
                    continue
                if raw.startswith("a/") or raw.startswith("b/"):
                    raw = raw[2:]
                p = Path(raw)
                if p.is_absolute() or ".." in p.parts:
                    raise PermissionError(f"unsafe patch path: {raw}")
                if raw not in paths:
                    paths.append(raw)
        if not paths:
            raise ValueError("patch contains no file paths")
        return paths

    def patch_preview(self, actor: str, repo_id: int, patch: str) -> dict[str, Any]:
        args = {"patch_sha256": self.ctx.storage.sha(patch)}
        with self.ctx.record(actor, repo_id, "patch_preview", "patch", args):
            paths = self._patch_paths(patch)
            root = self.ctx.repos.root(repo_id)
            for p in paths:
                self.ctx.repos.resolve(repo_id, p)
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as fh:
                fh.write(patch)
                temp = fh.name
            try:
                proc = subprocess.run(["git", "apply", "--check", temp], cwd=root, capture_output=True, text=True, timeout=30)
            finally:
                Path(temp).unlink(missing_ok=True)
            return {"valid": proc.returncode == 0, "paths": paths, "stderr": proc.stderr.strip(), "diff": patch}

    def apply_patch(self, actor: str, repo_id: int, patch: str) -> dict[str, Any]:
        args = {"patch_sha256": self.ctx.storage.sha(patch)}
        with self.ctx.record(actor, repo_id, "patch_apply", "patch", args):
            paths = self._patch_paths(patch)
            self.ctx.permissions.require(actor, repo_id, "edit", ",".join(paths), args, suggested_pattern="*")
            root = self.ctx.repos.root(repo_id)
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as fh:
                fh.write(patch)
                temp = fh.name
            try:
                check = subprocess.run(["git", "apply", "--check", temp], cwd=root, capture_output=True, text=True, timeout=30)
                if check.returncode:
                    raise ValueError(check.stderr.strip() or "git apply --check failed")
                apply = subprocess.run(["git", "apply", temp], cwd=root, capture_output=True, text=True, timeout=30)
                if apply.returncode:
                    raise RuntimeError(apply.stderr.strip() or "git apply failed")
            finally:
                Path(temp).unlink(missing_ok=True)
            patch_id = self.ctx.storage.add_patch(repo_id, actor, "patch_apply", patch, paths)
            return {"applied": True, "paths": paths, "patch_id": patch_id, "diff": patch}

    def revert_patch(self, actor: str, patch_id: int) -> dict[str, Any]:
        patch_row = self.ctx.storage.get_patch(patch_id)
        if not patch_row:
            raise KeyError(f"unknown patch: {patch_id}")
        repo_id = int(patch_row["repo_id"])
        args = {"patch_id": patch_id}
        with self.ctx.record(actor, repo_id, "patch_revert", str(patch_id), args):
            self.ctx.permissions.require(actor, repo_id, "edit", f"patch:{patch_id}", args, suggested_pattern="*")
            if patch_row["reverted"]:
                raise ValueError("patch is already marked reverted")
            root = self.ctx.repos.root(repo_id)
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as fh:
                fh.write(patch_row["patch"])
                temp = fh.name
            try:
                check = subprocess.run(["git", "apply", "-R", "--check", temp], cwd=root, capture_output=True, text=True, timeout=30)
                if check.returncode:
                    raise ValueError(check.stderr.strip() or "reverse patch check failed")
                proc = subprocess.run(["git", "apply", "-R", temp], cwd=root, capture_output=True, text=True, timeout=30)
                if proc.returncode:
                    raise RuntimeError(proc.stderr.strip() or "reverse patch failed")
            finally:
                Path(temp).unlink(missing_ok=True)
            self.ctx.storage.mark_patch_reverted(patch_id)
            reversed_patch = self._reverse_patch_for_display(patch_row["patch"])
            return {"reverted": True, "patch_id": patch_id, "paths": patch_row["changed_paths"], "diff": reversed_patch}
