import unittest
from unittest.mock import Mock, patch

from scripts import push_today_updates as push


class PushTodayUpdatesTests(unittest.TestCase):
    def test_uses_readme_statistics_date(self):
        readme = '<summary><strong>今日更新</strong>（1 个项目）</summary>统计日期：2026-10-26（UTC）</details>'
        projects = [
            {"full_name": "owner/app", "description": "应用简介", "latest_hap_release": {
                "published_at": "2026-10-26T12:00:00Z", "tag_name": "v1", "html_url": "https://example.com/release"}},
            {"full_name": "owner/other", "latest_hap_release": {"published_at": "2026-10-27T00:00:00Z"}},
        ]
        title, message = push.build_message(readme, projects)
        self.assertIn("2026-10-26", title)
        for value in ("owner/app", "v1", "应用简介", "https://example.com/release", "共 1 个项目更新"):
            self.assertIn(value, message)
        self.assertNotIn("owner/other", message)

    def test_no_updates(self):
        title, message = push.build_message('<summary><strong>今日更新</strong>（0 个项目）</summary>暂无更新。</details>', [])
        self.assertEqual(message, "今日暂无更新。")

    @patch.object(push.requests, "post")
    def test_post_and_business_status(self, post):
        post.return_value = Mock()
        post.return_value.json.return_value = {"status": 200}
        push.send_message("https://example.com/", "标题", "中文内容")
        self.assertEqual(post.call_args.kwargs["json"]["msg"], "中文内容")
        self.assertEqual(post.call_args.kwargs["timeout"], 30)
        post.return_value.json.return_value = {"status": 429}
        with self.assertRaises(ValueError):
            push.send_message("https://example.com/", "标题", "内容")


if __name__ == "__main__":
    unittest.main()
