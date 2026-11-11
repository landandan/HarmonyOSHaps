import io
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import Mock, patch

from scripts import push_today_updates as push


class PushTodayUpdatesTests(unittest.TestCase):
    def test_latest_release_matches_readme_even_without_new_hap(self):
        readme = '<summary><strong>今日更新</strong>（1 个项目）</summary>统计日期：2026-11-02（UTC）</details>'
        project = {"full_name": "owner/app", "latest_release": {
            "published_at": "2026-11-02T03:00:00Z", "tag_name": "v2",
            "html_url": "https://example.com/v2",
        }, "latest_hap_release": {
            "published_at": "2026-08-23T00:00:00Z", "tag_name": "v1",
        }}
        _, message = push.build_message(readme, [project])
        self.assertIn("共 1 个项目更新", message)
        self.assertIn("owner/app · v2", message)
        self.assertIn("https://example.com/v2", message)
        # 当 HAP 日期命中而最新 Release 日期不命中时，也不得推送旧版本。
        _, message = push.build_message(readme.replace("2026-11-02", "2026-08-23"), [project])
        self.assertIn("共 0 个项目更新", message)

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

    @patch.object(push.requests, "post")
    def test_safe_error_details(self, post):
        secret = "https://example.com/private-nickname/"
        response = Mock(status_code=503)
        cases = [
            (push.requests.Timeout(secret), "请求超时"),
            (push.requests.ConnectionError(secret), "连接失败"),
            (push.requests.HTTPError(secret, response=response), "HTTP 状态码 503"),
            (push.requests.exceptions.InvalidURL(secret), "请求失败"),
        ]
        for error, expected in cases:
            with self.subTest(error=type(error).__name__):
                post.side_effect = error
                with self.assertRaises(push.PushError) as caught:
                    push.send_message(secret, "标题", "内容")
                self.assertIn(expected, str(caught.exception))
                self.assertNotIn(secret, str(caught.exception))

    @patch.object(push.requests, "post")
    def test_business_codes_and_malformed_responses(self, post):
        response = post.return_value
        for code, expected in [(400, "参数错误"), (403, "内容策略"),
                               (404, "昵称未注册"), (429, "频率超限"),
                               (500, "服务器错误"), (999, "未知业务错误")]:
            with self.subTest(code=code):
                response.json.return_value = {"status": code, "msg": "private-nickname"}
                with self.assertRaises(push.PushError) as caught:
                    push.send_message("https://example.com/", "标题", "内容")
                self.assertIn(str(code), str(caught.exception))
                self.assertIn(expected, str(caught.exception))
                self.assertNotIn("private-nickname", str(caught.exception))
        for result in [None, [], {}, {"status": "200"}, {"status": []}]:
            with self.subTest(result=result):
                response.json.return_value = result
                with self.assertRaises(push.PushError):
                    push.send_message("https://example.com/", "标题", "内容")
        response.json.side_effect = ValueError("private response body")
        with self.assertRaisesRegex(push.PushError, "非 JSON"):
            push.send_message("https://example.com/", "标题", "内容")

    @patch.object(push, "build_message", return_value=("标题", "内容"))
    @patch.object(push, "send_message")
    @patch("sys.argv", ["push_today_updates.py"])
    def test_main_url_selection_and_failure_log(self, send, build):
        for configured in ["", "   ", " https://example.com/custom/ "]:
            with self.subTest(configured=configured), patch.dict(
                push.os.environ, {"MEOW_PUSH_URL": configured}
            ), redirect_stdout(io.StringIO()):
                push.main()
                send.assert_called_with(configured.strip() or push.DEFAULT_PUSH_URL, "标题", "内容")
        send.side_effect = push.PushError("业务状态码 429：发送或 IP 频率超限")
        stderr = io.StringIO()
        with redirect_stderr(stderr), redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as caught:
                push.main()
        self.assertEqual(caught.exception.code, 1)
        self.assertIn("429", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
