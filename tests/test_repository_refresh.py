"""已收录仓库持续刷新，并合并迁移前的仓库名。"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import collect


class RepositoryRefreshTests(unittest.TestCase):
    def test_cached_project_upgrades_and_paginates_without_losing_latest_release(self):
        first_page = [
            {"tag_name": f"v{20-i}", "published_at": f"2026-11-{20-i:02d}T00:00:00Z",
             "assets": [{"name": "app.zip"}]} for i in range(10)
        ]
        old_hap = {"tag_name": "v1", "published_at": "2026-01-01T00:00:00Z",
                   "assets": [{"name": "app.hap"}]}
        with tempfile.TemporaryDirectory(dir=collect.ROOT) as directory:
            root = Path(directory)
            with (
                patch.object(collect, "DATA_DIR", root),
                patch.object(collect, "PROJECTS_FILE", root / "projects.json"),
                patch.object(collect, "STATE_FILE", root / "state.json"),
                patch.object(collect, "update_readme", return_value=False),
                patch.object(collect, "load_category_overrides", return_value={}),
                patch.object(collect, "load_seeds", return_value=set()),
                patch.object(collect, "discover_candidates", return_value=set()),
                patch.object(collect, "api_get", side_effect=[
                    (first_page, "new-etag", True),
                    ([old_hap], None, True),
                    ({"full_name": "owner/app"}, None, True),
                ]) as api,
                patch("sys.argv", ["collect.py"]),
            ):
                collect.save_json(collect.PROJECTS_FILE, [{
                    "full_name": "owner/app", "latest_hap_release": old_hap,
                }])
                collect.save_json(collect.STATE_FILE, {
                    "candidates": {"owner/app": {"has_hap": True, "etag": "old-etag"}},
                    "processed_event_ids": [],
                })
                collect.main()
                project = collect.load_json(collect.PROJECTS_FILE, [])[0]
                self.assertNotIn("etag", api.call_args_list[0].kwargs)
                self.assertEqual(api.call_args_list[1].kwargs["params"]["page"], 2)
                self.assertEqual(project["latest_release"]["tag_name"], "v20")
                self.assertEqual(project["latest_hap_release"]["tag_name"], "v1")

    def test_drafts_are_excluded_from_latest_release(self):
        with patch.object(collect, "api_get", return_value=({"full_name": "owner/app"}, None, True)):
            project, _ = collect.inspect_releases("owner/app", [
                {"tag_name": "draft", "draft": True, "published_at": "2026-11-20T00:00:00Z"},
                {"tag_name": "public", "published_at": "2026-11-19T00:00:00Z",
                 "assets": [{"name": "app.hap"}]},
            ])
        self.assertEqual(project["latest_release"]["tag_name"], "public")

    def test_existing_project_refreshes_and_uses_canonical_name(self):
        old = "old/app"
        new = "new/app"
        release = {
            "tag_name": "2.0.1", "published_at": "2026-11-02T03:36:14Z",
            "assets": [{"name": "app.hap"}],
        }
        with tempfile.TemporaryDirectory(dir=collect.ROOT) as directory:
            root = Path(directory)
            with (
                patch.object(collect, "DATA_DIR", root),
                patch.object(collect, "PROJECTS_FILE", root / "projects.json"),
                patch.object(collect, "STATE_FILE", root / "state.json"),
                patch.object(collect, "README_FILE", root / "README.md"),
                patch.object(collect, "load_category_overrides", return_value={}),
                patch.object(collect, "load_seeds", return_value=set()),
                patch.object(collect, "discover_candidates", return_value=set()),
                patch.object(collect, "api_get", side_effect=[
                    ([release], "etag", True),
                    ({"full_name": new, "html_url": f"https://github.com/{new}"}, None, True),
                ]) as api,
                patch("sys.argv", ["collect.py"]),
            ):
                collect.save_json(collect.PROJECTS_FILE, [{"full_name": old}])
                collect.main()
                projects = collect.load_json(collect.PROJECTS_FILE, [])
                self.assertEqual([p["full_name"] for p in projects], [new])
                self.assertEqual(projects[0]["latest_hap_release"]["tag_name"], "2.0.1")
                state = collect.load_json(collect.STATE_FILE, {})["candidates"]
                self.assertNotIn(old, state)
                self.assertIn(new, state)
                self.assertEqual(api.call_count, 2)

    def test_newest_release_can_use_zip_while_hap_metadata_is_preserved(self):
        releases = [
            {"tag_name": "2.0.1", "published_at": "2026-11-02T03:36:14Z",
             "assets": [{"name": "app.zip"}]},
            {"tag_name": "1.2.2", "published_at": "2026-08-23T00:00:00Z",
             "assets": [{"name": "app.hap"}]},
        ]
        with patch.object(collect, "api_get", return_value=({"full_name": "new/app"}, None, True)):
            project, has_hap = collect.inspect_releases("old/app", releases)
        self.assertTrue(has_hap)
        self.assertEqual(project["latest_release"]["tag_name"], "2.0.1")
        self.assertEqual(project["latest_hap_release"]["tag_name"], "1.2.2")
        with patch.object(collect, "load_category_overrides", return_value={}):
            self.assertIn("2.<wbr>0.<wbr>1", collect.build_table([project]))
