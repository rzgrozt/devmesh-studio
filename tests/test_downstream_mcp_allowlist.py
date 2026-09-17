from __future__ import annotations

import unittest

from devmesh_studio.tools.downstream_mcp import _tool_allowed


class DownstreamMCPAllowlistTests(unittest.TestCase):
    def test_wildcard_allows_every_tool(self):
        self.assertTrue(_tool_allowed("health_report", ["*"]))
        self.assertTrue(_tool_allowed("browser_click", ["*"]))

    def test_exact_name_still_works(self):
        self.assertTrue(_tool_allowed("list_windows", ["list_windows"]))
        self.assertFalse(_tool_allowed("kill_app", ["list_windows"]))

    def test_shell_style_patterns_are_supported(self):
        self.assertTrue(_tool_allowed("browser_click", ["browser_*"]))
        self.assertTrue(_tool_allowed("browser_type", ["browser_*"]))
        self.assertFalse(_tool_allowed("list_apps", ["browser_*"]))

    def test_empty_allowlist_denies(self):
        self.assertFalse(_tool_allowed("health_report", []))


if __name__ == "__main__":
    unittest.main()
