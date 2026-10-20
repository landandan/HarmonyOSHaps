#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
收集在 GitHub Release 中发布 `.hap`（HarmonyOS 应用包）附件的开源项目。

流水线：
  1. 发现候选仓库：手工种子（seeds.json）+ GitHub 搜索 API + 公共 Release 事件。
  2. 逐个检查候选仓库的最近 Release，寻找带 `.hap` 附件的 Release。
  3. 将命中的项目写入 data/projects.json，检查状态写入 data/state.json。
  4. 在 README.md 的 HAP_TABLE 标记之间重新生成导航表格（按发布时间倒序）。

唯一依赖：requests（见 requirements.txt）。
认证：环境变量 GITHUB_TOKEN（强烈建议设置，CI 中自动可用）。
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

API_BASE = "https://api.github.com"

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
SEEDS_FILE = DATA_DIR / "seeds.json"
STATE_FILE = DATA_DIR / "state.json"
PROJECTS_FILE = DATA_DIR / "projects.json"
CATEGORIES_FILE = DATA_DIR / "categories.json"
README_FILE = ROOT / "README.md"

SEARCH_QUERIES = [
    # 生态话题标签
    "topic:harmonyos",
    "topic:openharmony",
    "topic:ohos",
    # 英文关键词（名称/描述）
    "harmonyos in:name,description",
    "openharmony in:name,description",
    "ohos in:name,description",  # 大量项目用 -ohos 后缀（如 FlClash-ohos）
    "hap in:name,description",
    # 中文关键词（国内项目描述常用）
    "鸿蒙 in:name,description",
]

SEARCH_PAGE_SIZE = 100
SEARCH_MAX_PAGES = 10
SEARCH_PACING_SECONDS = 3  # 搜索 API 每次请求之间至少间隔 3 秒（限速 30 次/分钟）
RELEASES_PER_PAGE = 10
EVENTS_PER_PAGE = 100
PROCESSED_EVENT_IDS_LIMIT = 2000  # 只保留最近的事件 id，防止列表无限膨胀
RE_CHECK_INTERVAL_SECONDS = 24 * 3600  # 无 hap 的仓库 24 小时内不重复复查，控制配额

TABLE_START = "<!-- HAP_TABLE_START -->"
TABLE_END = "<!-- HAP_TABLE_END -->"

TOKEN = os.environ.get("GITHUB_TOKEN", "").strip()


def now_iso():
    """当前 UTC 时间的 ISO8601 字符串。"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(value):
    """解析 ISO8601 时间字符串为 Unix 时间戳，失败返回 0.0。"""
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return 0.0


def format_stars(count):
    """Stars 千分位格式化，如 1234567 -> 1,234,567。"""
    try:
        return f"{int(count):,}"
    except (TypeError, ValueError):
        return "0"


def human_size(size):
    """文件大小人类可读格式化：B / KB / MB / GB。"""
    if size is None:
        return ""
    try:
        size = int(size)
    except (TypeError, ValueError):
        return ""
    if size < 1024:
        return f"{size} B"
    for unit in ("KB", "MB", "GB"):
        size /= 1024.0
        if size < 1024 or unit == "GB":
            return f"{size:.1f} {unit}"
    return f"{size:.1f} GB"


def truncate(text, limit):
    """超长文本截断并追加省略号。"""
    text = str(text)
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def wbr_wrap(text, chunk=16):
    """在长文本中插入 <wbr> 断行点，避免 GitHub 表格横向溢出。

    GitHub 表格无法设置列宽，横向溢出只能靠内容可断行解决。
      - 含空格的文本（如简介）：浏览器在空格处自然换行，仅补 - _ . / 分隔符断点，不强制断词
      - 含中文的无空格文本：浏览器可任意断行，同样仅补分隔符断点
      - 纯 ASCII 连续长串（文件名/tag）：在分隔符后断开，超长连续段按 chunk 兜底断开
    """
    text = str(text)
    if " " in text or re.search(r"[\u4e00-\u9fff]", text):
        return re.sub(r"([\-_./])", r"\1<wbr>", text)
    parts, seg = [], ""
    for ch in text:
        seg += ch
        if ch in "-_./" or len(seg) >= chunk:
            parts.append(seg)
            seg = ""
    if seg:
        parts.append(seg)
    return "<wbr>".join(parts)


def load_json(path, default):
    """读取 JSON 文件，文件缺失或损坏时返回默认值。"""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def save_json(path, data):
    """以 UTF-8 写入 JSON 文件。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def api_get(url, params=None, etag=None):
    """GitHub API GET 请求封装。

    返回 (json_data, new_etag, changed)：
      - 304 Not Modified -> (None, etag, False)，不消耗配额
      - 403 速率限制     -> 等待 X-RateLimit-Reset（或 Retry-After）后重试
      - 其他非 2xx      -> 抛出 HTTPError
      - 成功            -> (json, ETag 头, True)
    """
    headers = {
        "Accept": "application/vnd.github+json",
    }
    if TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"
    if etag:
        headers["If-None-Match"] = etag

    while True:
        resp = requests.get(url, params=params, headers=headers, timeout=30)

        if resp.status_code == 304:
            return None, etag, False

        if resp.status_code == 403:
            wait = 0
            reset = resp.headers.get("X-RateLimit-Reset")
            if reset:
                try:
                    wait = int(reset) - time.time() + 1
                except ValueError:
                    wait = 0
            if wait > 0:
                print(f"[rate-limit] sleeping {wait:.0f}s until reset")
                time.sleep(wait)
                continue
            retry_after = resp.headers.get("Retry-After")
            if retry_after:
                try:
                    delay = int(retry_after)
                except ValueError:
                    delay = 60
                print(f"[rate-limit] retrying after {delay}s")
                time.sleep(delay)
                continue
            resp.raise_for_status()

        if not resp.ok:
            resp.raise_for_status()

        return resp.json(), resp.headers.get("ETag"), True


def discover_release_events(state):
    """从公共事件流中发现新发布的 ReleaseEvent，并记录已处理事件 id。

    返回去重后的候选仓库集合（full_name）。
    """
    data, _, _ = api_get(f"{API_BASE}/events", params={"per_page": EVENTS_PER_PAGE})
    processed = set(state["processed_event_ids"])
    candidates = set()
    new_ids = []
    for event in data or []:
        eid = event.get("id")
        if eid is None:
            continue
        if eid in processed:
            continue
        new_ids.append(eid)
        if (
            event.get("type") == "ReleaseEvent"
            and (event.get("payload") or {}).get("action") == "published"
        ):
            repo = event.get("repo") or {}
            if repo.get("name"):
                candidates.add(repo["name"])
    if new_ids:
        # 事件流按最新在前返回，保留最近的部分即可
        state["processed_event_ids"] = (
            state["processed_event_ids"] + new_ids
        )[-PROCESSED_EVENT_IDS_LIMIT:]
    return candidates


def discover_candidates(state):
    """合并 seeds + 搜索结果 + Release 事件，得到去重的候选仓库集合。"""
    candidates = set()

    # 1. 手工种子仓库
    seeds = load_json(SEEDS_FILE, [])
    if isinstance(seeds, list):
        for name in seeds:
            if isinstance(name, str) and name.strip() and "/" in name:
                candidates.add(name.strip())
    print(f"[discover] {len(candidates)} candidates from seeds")

    # 2. GitHub 搜索（每次请求至少间隔 3 秒，页数不足 100 提前结束）
    first_request = True
    for query in SEARCH_QUERIES:
        for page in range(1, SEARCH_MAX_PAGES + 1):
            if not first_request:
                time.sleep(SEARCH_PACING_SECONDS)
            first_request = False
            data, _, _ = api_get(
                f"{API_BASE}/search/repositories",
                params={"q": query, "per_page": SEARCH_PAGE_SIZE, "page": page},
            )
            items = (data or {}).get("items") or []
            for item in items:
                full_name = item.get("full_name")
                if full_name:
                    candidates.add(full_name)
            print(f"[search] q='{query}' page={page} -> {len(items)} repos")
            if len(items) < SEARCH_PAGE_SIZE:
                break

    # 3. 公共 Release 事件
    event_candidates = discover_release_events(state)
    candidates |= event_candidates
    print(f"[events] {len(event_candidates)} new release events")

    return candidates


def inspect_releases(full_name, releases):
    """在 Release 列表中找到最新（按发布时间）带 `.hap` 附件的 Release。

    返回 (project_or_None, has_hap)。
    """
    ordered = sorted(
        releases or [],
        key=lambda r: (r.get("published_at") or ""),
        reverse=True,
    )
    for release in ordered:
        if release.get("draft"):
            continue
        hap_assets = [
            a
            for a in (release.get("assets") or [])
            if (a.get("name") or "").lower().endswith(".hap")
        ]
        if not hap_assets:
            continue

        # 命中：拉取仓库基本信息
        info, _, _ = api_get(f"{API_BASE}/repos/{full_name}")
        project = {
            "full_name": full_name,
            "html_url": info.get("html_url") or f"https://github.com/{full_name}",
            "description": info.get("description") or "",
            "stars": info.get("stargazers_count") or 0,
            "latest_hap_release": {
                "tag_name": release.get("tag_name") or "",
                "published_at": release.get("published_at") or "",
                "html_url": release.get("html_url") or "",
                "assets": [
                    {
                        "name": a.get("name"),
                        "browser_download_url": a.get("browser_download_url"),
                        "size": a.get("size"),
                    }
                    for a in hap_assets
                ],
            },
        }
        return project, True

    return None, False


CATEGORIES = ("工具与实用程序", "办公", "影音娱乐", "阅读", "代理工具", "游戏", "开发", "网络浏览器", "社交网络", "其他")

# 按用途匹配；专用应用优先于通用工具，无法识别的项目归入“其他”。
CATEGORY_RULES = (
    # 明确用途优先；同步目录、模型引擎、UI 技术等实现细节不代表应用用途。
    ("工具与实用程序", r"syncthing|文件同步|file.{0,20}sync|traccar|GPS.{0,12}(?:track|定位)|定位追踪|去水印|watermark.{0,12}remov|hap[-_ ]installer|hap[-_ ]release[-_ ]kit|签名安装套件|应用签名|为鸿蒙应用签名"),
    ("工具与实用程序", r"本地.{0,8}AI.{0,8}对话|local[-_ ]ai[-_ ]chat|ai.{0,4}智能体.{0,30}个人助理|本地大模型工具箱"),
    ("代理工具", r"vpn|clash|代理|proxy|sing.?box|tailscale|harmotail|easytier|etohos|karing|v2ray|xray|shadowsocks|hysteria|nekobox"),
    ("游戏", r"游戏|\bgame\b|gaming|mindustry|minecraft|minecaft|homolauncher|pvz|emulator|模拟器|hps2|snake|pokedex|宝可梦|buttercup|joiplay|tyranor|mcb_ohos"),
    ("阅读", r"阅读|小说|漫画|电子书|词典|字典|manga|comic|cimoc|ehviewer|ehentai|e-hentai|exhentai|nhentai|picacg|bika|mybooks|copyhomo|copi-ohos|nextn\b|nexte\b|mdreader|ebook|epub|mdict|dictionar"),
    ("开发", r"开发框架|开发工具|工具链|项目模板|测试靶场|客户端测试|调试|debug|\bide\b|\bdemo\b|\bsample\b|pipeline|github.action|github client|arkcat|终端|terminal|ssh|codex|harness|harnes|opencode|zcode|tauri.{0,12}(?:api[-_ ]*)?test|llama\.cpp[-_ ]server|自动化构建|示例|开发课|ngf\b"),
    ("网络浏览器", r"browser|浏览器|webapp|webview app|nas_nav"),
    ("影音娱乐", r"music|player|emby|jellyfin|anime|pili|podcast|antennapod|homenapod|harflix|pixiv|pixlume|音乐|视频|影音|播放|动漫|番剧|直播|播客|相册|photo|immich|melotopia"),
    ("社交网络", r"telegram|petrelgram|社交|论坛|社区|贴吧|知乎|米游社|linux.?do|hipda|4d4y|stage1st|s1-orange|lnga|\bnga\b|tieba|zhihu|harmonyx|\bx client\b|mastodon|misskey|weibo|微博|fishpi|摸鱼派|c001apk"),
    ("办公", r"邮件|mail|笔记|note|markdown|日历|calendar|待办|todo|办公|office|文档|document|pdf|翻译|translat|剪贴板|clipboard|cliplink|记账|账本|tally"),
    ("工具与实用程序", r"搜图|工具|tool|utility|计算器|calculator|文件|file|同步|sync|localsend|输入法|fcitx|密码|password|auth|totp|vpn|clash|代理|tailscale|harmotail|easytier|etohos|karing|终端|terminal|ssh|codex|deepseek|harness|opencode|zcode|安装|install|下载|download|aria2|监控|monitor|benchmark|调试|debug|录屏|录音|record|远程|remote|homeassistant|米家|miha_hm|openwrt|磁盘|disk|spaceow|github client|arkcat|ai.?chat|ai.?助手|大模型|llama|kelivo|nextai|智能家居|识别|vision.assist|摄像|ipcam"),
)


def load_category_overrides():
    """读取人工分类；配置错误时明确报错，避免静默使用自动分类。"""
    if not CATEGORIES_FILE.exists():
        return {}
    with CATEGORIES_FILE.open(encoding="utf-8") as fh:
        config = json.load(fh)
    if not isinstance(config, dict):
        raise ValueError("data/categories.json 必须是仓库全名到分类的 JSON 对象")
    overrides = {}
    for name, category in config.items():
        if not re.fullmatch(r"[^/\s]+/[^/\s]+", name):
            raise ValueError(f"无效仓库全名：{name!r}，应为 owner/repo")
        if category not in CATEGORIES:
            raise ValueError(f"{name} 的分类无效：{category!r}；可选分类：{'、'.join(CATEGORIES)}")
        key = name.casefold()
        if key in overrides:
            raise ValueError(f"重复的仓库分类配置：{name}")
        overrides[key] = category
    return overrides


def classify_project(project, overrides=None):
    """根据仓库名和完整简介分类，也支持数据中显式指定 category。"""
    override = (overrides or {}).get((project.get("full_name") or "").casefold())
    if override is not None:
        return override
    category = project.get("category")
    if category in CATEGORIES:
        return category
    repo = (project.get("full_name") or "").partition("/")[2]
    text = f"{repo} {project.get('description') or ''}"
    for category, pattern in CATEGORY_RULES:
        if re.search(pattern, text, re.IGNORECASE):
            return category
    return "其他"


SECTION_ANCHORS = dict(zip(
    ("今日更新", *CATEGORIES),
    ("today", "utilities", "office", "media", "reading", "proxy", "games",
     "development", "browsers", "social", "other"),
))


def build_table(projects):
    """生成分类 README 表格（各类内按最新 HAP Release 发布时间降序）。

    布局优化（GitHub 表格无法设列宽，靠对齐语法 + <wbr> 断行实现）：
      - 项目列：仓库名加粗 + 所属者第二行小字，列宽紧凑
      - Stars / 更新日期 / 最新版本 居中
      - 最新版本列窄化：tag 截断 30 字符并允许断行
      - 简介 / tag 插入 <wbr>，内容可换行，整表不横向滚动
      - 不含下载列：HAP 附件数据仍完整保存在 data/projects.json
    """
    overrides = load_category_overrides()
    ordered = sorted(
        projects,
        key=lambda p: (p.get("latest_hap_release") or {}).get("published_at") or "",
        reverse=True,
    )
    generated_at = now_iso()
    today = generated_at[:10]
    rows = [
        "<!-- 自动生成，请勿手动编辑 -->",
        f"**共收录 {len(ordered)} 个项目** · 更新于 {generated_at[:16]} (UTC)",
        "",
    ]
    grouped = {category: [] for category in CATEGORIES}
    for project in ordered:
        grouped[classify_project(project, overrides)].append(project)
    # 今日更新是额外汇总，项目仍保留在原分类中，不计入分类总数。
    grouped["今日更新"] = [
        project for project in ordered
        if ((project.get("latest_hap_release") or {}).get("published_at") or "")[:10] == today
    ]
    rows.extend(['<a id="hap-menu" name="hap-menu"></a>', ""])
    sections = ("今日更新", *CATEGORIES)
    for category in sections:
        count = len(grouped[category])
        rows.append(
            f"- [{category}：{count}](#hap-{SECTION_ANCHORS[category]})"
        )
    current_category = None
    for category, p in ((category, p) for category in sections for p in (grouped[category] or [None])):
        if category != current_category:
            if current_category is not None:
                rows.extend(["", "[↑ 返回菜单](#hap-menu)", "", "</details>"])
            rows.extend([
                "",
                f'<a id="hap-{SECTION_ANCHORS[category]}" name="hap-{SECTION_ANCHORS[category]}"></a>',
                "",
                "<details open>",
                f"<summary><strong>{category}</strong>（{len(grouped[category])} 个项目）</summary>",
                "",
                "| 项目 | 简介 | ⭐ Stars | 📅 更新日期 | 🏷️ 最新版本 |",
                "|:-----|:-----|:-------:|:-----------:|:------------|",
            ])
            current_category = category
            if category == "今日更新":
                rows[-2:-2] = [f"统计日期：{today}（UTC），项目同时保留在下方原分类中。", ""]
        if p is None:
            rows.extend(["", "暂无更新。" if category == "今日更新" else "暂无项目。"])
            continue
        name = p.get("full_name", "")
        repo_url = p.get("html_url") or f"https://github.com/{name}"
        # 项目列：仓库名加粗，所属者换行小字（owner/repo 拆两行）
        owner, _, repo = name.partition("/")
        if repo:
            project_cell = f"[**{repo}**]({repo_url})<br><sub>{owner}</sub>"
        else:
            project_cell = f"[**{name}**]({repo_url})"
        # 简介：60 字符截断 + 省略号，允许断行
        desc = (p.get("description") or "").replace("\r", " ").replace("\n", " ")
        if len(desc) > 60:
            desc = desc[:57] + "…"
        desc = wbr_wrap(desc, 20).replace("|", "\\|").strip()
        if not desc:
            desc = "—"
        stars = format_stars(p.get("stars", 0))
        rel = p.get("latest_hap_release") or {}
        published = rel.get("published_at") or ""
        date = published[:10] or "—"  # YYYY-MM-DD
        # 最新版本：截断 30 字符 + 断行点，压缩列宽
        tag = truncate(rel.get("tag_name") or "", 30).replace("|", "\\|")
        tag = wbr_wrap(tag, 12)
        rel_url = rel.get("html_url") or ""
        rows.append(
            f"| {project_cell} | {desc} | {stars} "
            f"| {date} | [{tag}]({rel_url}) |"
        )
    if current_category is not None:
        rows.extend(["", "[↑ 返回菜单](#hap-menu)", "", "</details>"])
    return "\n".join(rows)


def update_readme(table):
    """用新表格替换 README 中 HAP_TABLE 标记之间的内容。返回是否有变更。"""
    if not README_FILE.exists():
        print(f"[readme] {README_FILE} not found, skipping")
        return False
    content = README_FILE.read_text(encoding="utf-8")
    if TABLE_START not in content or TABLE_END not in content:
        print("[readme] markers not found, skipping")
        return False
    head, _, rest = content.partition(TABLE_START)
    _, _, tail = rest.partition(TABLE_END)
    new_content = head + TABLE_START + "\n" + table + "\n" + TABLE_END + tail
    if new_content != content:
        README_FILE.write_text(new_content, encoding="utf-8")
        return True
    return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--readme-only", action="store_true", help="仅用本地项目数据和分类配置更新 README，不请求 GitHub")
    args = parser.parse_args()
    # 在采集前校验人工配置，避免配置错误导致耗时采集后才失败。
    load_category_overrides()
    if args.readme_only:
        projects = load_json(PROJECTS_FILE, [])
        if not isinstance(projects, list):
            raise ValueError("data/projects.json 必须是项目列表")
        changed = update_readme(build_table(projects))
        print(f"[readme] changed={changed}")
        return

    if not TOKEN:
        print("[warn] GITHUB_TOKEN is not set; API rate limits will be much lower")

    # 读取状态与既有项目
    state = load_json(STATE_FILE, {"candidates": {}, "processed_event_ids": []})
    state.setdefault("candidates", {})
    state.setdefault("processed_event_ids", [])

    projects = load_json(PROJECTS_FILE, [])
    if not isinstance(projects, list):
        projects = []
    projects_map = {
        p["full_name"]: p for p in projects if isinstance(p, dict) and p.get("full_name")
    }

    candidates = discover_candidates(state)
    print(f"[discover] {len(candidates)} unique candidates")

    found = {}
    no_hap = set()
    skipped = 0
    for name in sorted(candidates):
        cand = state["candidates"].setdefault(name, {})
        # 已确认无 hap 且 24 小时内刚查过 -> 跳过，控制核心 API 配额
        if (
            not cand.get("has_hap")
            and RE_CHECK_INTERVAL_SECONDS
            and cand.get("last_checked_at")
        ):
            last_ts = parse_iso(cand["last_checked_at"])
            if last_ts and (time.time() - last_ts) < RE_CHECK_INTERVAL_SECONDS:
                skipped += 1
                continue
        try:
            # 已确认有 hap 且 ETag 未变化 -> 跳过，节省配额
            if cand.get("has_hap") and cand.get("etag"):
                releases, etag, changed = api_get(
                    f"{API_BASE}/repos/{name}/releases",
                    params={"per_page": RELEASES_PER_PAGE},
                    etag=cand["etag"],
                )
                cand["last_checked_at"] = now_iso()
                if not changed:
                    skipped += 1
                    continue
                cand["etag"] = etag
            else:
                releases, etag, _ = api_get(
                    f"{API_BASE}/repos/{name}/releases",
                    params={"per_page": RELEASES_PER_PAGE},
                )
                cand["etag"] = etag
                cand["last_checked_at"] = now_iso()
        except requests.exceptions.HTTPError as exc:
            # 搜索/种子中混入已删除或已改名的仓库，跳过而非中断
            status = exc.response.status_code if exc.response is not None else None
            if status == 404:
                print(f"[skip] {name} not found (404)")
                cand["last_checked_at"] = now_iso()
                continue
            raise

        project, has_hap = inspect_releases(name, releases)
        cand["has_hap"] = bool(has_hap)  # 记录检查结论，供后续 ETag 跳过判断使用
        if has_hap and project:
            found[name] = project
        else:
            no_hap.add(name)
        print(f"[check] {name}: has_hap={has_hap}")

    # 合并：保留既有项目并更新；仅当本轮明确复查确认无 hap 时才移除
    for name in no_hap:
        projects_map.pop(name, None)
    projects_map.update(found)
    final = sorted(
        projects_map.values(),
        key=lambda p: (p.get("latest_hap_release") or {}).get("published_at") or "",
        reverse=True,
    )

    save_json(PROJECTS_FILE, final)
    save_json(STATE_FILE, state)

    table = build_table(final)
    readme_changed = update_readme(table)
    print(
        f"[done] candidates={len(candidates)} skipped_unchanged={skipped} "
        f"projects={len(final)} readme_changed={readme_changed}"
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # CI 日志可见的错误出口
        print(f"[fatal] {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
