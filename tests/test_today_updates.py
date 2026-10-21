"""今日更新的 UTC 日期切换边界。"""

import unittest
from unittest.mock import patch

from scripts import collect


class TodayUpdatesTests(unittest.TestCase):
    def test_statistics_date_boundaries(self):
        for timestamp, expected in (
            ("2026-10-21T00:00:00Z", "2026-10-21"),
            ("2026-10-21T00:00:01Z", "2026-10-20"),
            ("2026-10-21T00:27:00Z", "2026-10-20"),
            ("2026-10-21T00:27:01Z", "2026-10-20"),
            ("2026-10-21T07:59:59Z", "2026-10-20"),
            ("2026-10-21T08:00:00Z", "2026-10-21"),
            ("2026-01-01T01:00:00Z", "2025-12-31"),
        ):
            with self.subTest(timestamp=timestamp), patch.object(collect, "now_iso", return_value=timestamp):
                projects = [
                    {"full_name": "example/previous", "latest_hap_release": {"published_at": "2026-10-20T12:00:00Z"}},
                    {"full_name": "example/current", "latest_hap_release": {"published_at": "2026-10-21T00:00:00Z"}},
                ]
                table = collect.build_table(projects)
                self.assertIn(f"统计日期：{expected}（UTC）", table)
                today_section = table.split('<summary><strong>今日更新</strong>', 1)[1].split('</details>', 1)[0]
                if expected == "2026-10-20":
                    self.assertIn("**previous**", today_section)
                    self.assertNotIn("**current**", today_section)
                elif expected == "2026-10-21":
                    self.assertIn("**current**", today_section)
                    self.assertNotIn("**previous**", today_section)


if __name__ == "__main__":
    unittest.main()
