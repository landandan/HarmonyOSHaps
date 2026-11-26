"""黑名单不能被搜索、种子或既有缓存重新收录。"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import collect


class BlocklistTests(unittest.TestCase):
    def test_blocked_candidates_and_cached_projects_are_removed(self):
        with tempfile.TemporaryDirectory(dir=collect.ROOT) as directory:
            root = Path(directory)
            with (
                patch.object(collect, "PROJECTS_FILE", root / "projects.json"),
                patch.object(collect, "STATE_FILE", root / "state.json"),
                patch.object(collect, "load_blocklist", return_value={"owner/app"}),
                patch.object(collect, "load_seeds", return_value={"Owner/App"}),
                patch.object(collect, "discover_candidates", return_value={"Owner/App"}),
                patch.object(collect, "api_get") as api,
                patch.object(collect, "update_readme", return_value=False),
                patch("sys.argv", ["collect.py"]),
            ):
                collect.save_json(collect.PROJECTS_FILE, [{"full_name": "OWNER/APP"}])
                collect.save_json(collect.STATE_FILE, {"candidates": {"Owner/App": {}}})
                collect.main()
                api.assert_not_called()
                self.assertEqual(collect.load_json(collect.PROJECTS_FILE, None), [])
                self.assertEqual(collect.load_json(collect.STATE_FILE, {})["candidates"], {})

    def test_render_filters_blocked_projects_and_count(self):
        with patch.object(collect, "load_blocklist", return_value={"owner/app"}):
            table = collect.build_table([
                {"full_name": "Owner/App"}, {"full_name": "allowed/app"},
            ])
        self.assertNotIn("Owner/App", table)
        self.assertIn("allowed/app", table)
        self.assertIn("共收录 1 个项目", table)
