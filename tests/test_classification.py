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

    def test_education_projects(self):
        projects = collect.load_json(collect.PROJECTS_FILE, [])
        overrides = collect.load_category_overrides()
        names = {
            "wuweiyouzuoju/jidecards-anki-harmonyos",
            "mmcc65/word-assistant",
            "PollenWang6/HiXD",
            "picklerick422/zju-learning-assistant-OH",
            "xiaomoHM/ShuaTi",
            "WheretoSleepinNJU/njuschedule-hmos",
            "zywbjj/duolinguo-harmonyos",
            "meowqwe/deepquest",
        }
        for project in projects:
            if project["full_name"] in names:
                with self.subTest(project=project["full_name"]):
                    self.assertEqual(collect.classify_project(project), "教育")
                    self.assertEqual(collect.classify_project(project, overrides), "教育")

    def test_learning_disclaimer_does_not_imply_education(self):
        self.assertEqual(collect.classify_project({
            "full_name": "example/music-player",
            "description": "音乐播放器，仅供学习交流",
        }), "影音娱乐")

    def test_education_navigation(self):
        table = collect.build_table([{
            "full_name": "example/flashcards", "description": "卡片学习客户端",
        }])
        self.assertIn("[教育：1](#hap-education)", table)
        self.assertIn('<a id="hap-education" name="hap-education"></a>', table)
        self.assertIn('<summary><strong>教育</strong>（1 个项目）</summary>', table)

    def test_primary_purpose_beats_incidental_keywords(self):
        cases = (
            ("pixez-flutter-ohos", "支持免代理直连的 Pixiv 插画客户端", "影音娱乐"),
            ("Next2V", "V2EX 客户端，支持浏览、阅读、回复", "社交网络"),
            ("istore-pilot", "iStoreOS 路由管理，包含浏览器控制台", "工具与实用程序"),
            ("RemoteDeskHarmonyOS", "远程桌面，支持 RDP / SSH / SFTP", "工具与实用程序"),
            ("novel-editor", "小说编辑与创作的写作软件", "办公"),
            ("desktop-pet", "桌宠，支持 AI 聊天、对话树终端", "影音娱乐"),
            ("Tesslo", "以 Scratch3 为核心的积木编程桌面应用", "教育"),
            ("dictionary", "Read MDict dictionaries", "教育"),
            ("campus", "校园服务：课表、教务查询、论坛和 PDF 通知", "教育"),
            ("campus-safety", "校园安全事件处置任务与角色协同办公", "办公"),
            ("moonlight", "游戏串流客户端，远程连接 PC", "游戏"),
            ("sample", "ArkUI 开发示例，仅供学习交流", "开发"),
        )
        for name, description, category in cases:
            with self.subTest(project=name):
                self.assertEqual(collect.classify_project({
                    "full_name": f"example/{name}", "description": description,
                }), category)

    def test_reviewed_reclassification_overrides(self):
        overrides = collect.load_category_overrides()
        expected = {
            "Your-USTC/DailyNic_HMOS": "教育",
            "tianlongbaobao/idut-HarmonyOS-Next": "教育",
            "GoodNima/DanXi_HarmonyOS": "教育",
            "JNGKZbird/Arknights-Angelina-Pet-YuYuan-HarmonyOS-NEXT": "影音娱乐",
            "l9970509/AggPay": "工具与实用程序",
            "VoidInHeart/DevControl_openHarmony": "其他",
        }
        for name, category in expected.items():
            with self.subTest(project=name):
                self.assertEqual(collect.classify_project({
                    "full_name": name, "description": "",
                }, overrides), category)


if __name__ == "__main__":
    unittest.main()
