"""用途分类回归：避免被实现技术、目录名或下载链接误导。"""

import unittest

from scripts import collect


class ClassificationTests(unittest.TestCase):
    def test_reviewed_projects(self):
        expected = {
            "simon2137/traccar-client-harmony": "工具与实用程序",
            "LMV475/hap-installer": "工具与实用程序",
            "forwardz666/Syncthing-for-HarmonyOS": "工具与实用程序",
            "kamalovecz/hap_release_kit": "工具与实用程序",
            "XCNXNXNX/lingxi-flow-agent": "工具与实用程序",
            "SKL-666666/mnn-local-ai-chat": "工具与实用程序",
            "littlestone0806/doubao-watermark-lab": "工具与实用程序",
            "sys2008/sys_AItoolkit_ohos": "工具与实用程序",
            "dgh1818/immich_ohos": "影音娱乐",
            "Aloereed/llama.cpp-server-ohos": "开发",
            "Islatri/tauri-ohos-test": "开发",
            "Turbo1123/turbo-ai-chat-harmonyos": "开发",
            "jschwe/ServoDemo": "开发",
        }
        projects = {p["full_name"]: p for p in collect.load_json(collect.PROJECTS_FILE, [])}
        for name, category in expected.items():
            with self.subTest(project=name):
                # 不传人工配置，确认修正来自自动规则。
                self.assertEqual(collect.classify_project(projects[name]), category)

    def test_implementation_details_do_not_imply_development(self):
        for description in (
            "An SDK-powered music player",
            "An Electron music player",
            "A Tauri music player",
        ):
            with self.subTest(description=description):
                self.assertEqual(collect.classify_project({
                    "full_name": "example/app", "description": description,
                }), "影音娱乐")

    def test_explicit_categories_still_take_priority(self):
        project = {"full_name": "example/syncthing", "category": "办公"}
        self.assertEqual(collect.classify_project(project), "办公")
        self.assertEqual(collect.classify_project(project, {
            "example/syncthing": "开发",
        }), "开发")


if __name__ == "__main__":
    unittest.main()
