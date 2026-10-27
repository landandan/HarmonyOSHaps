#!/usr/bin/env python3
"""将本轮 README 的今日更新汇总推送到 MeoW。"""

import argparse
import json
import os
from pathlib import Path
import re
import sys

import requests

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PUSH_URL = "https://api.chuckfang.com/261fa864/"


def build_message(readme, projects):
    # 使用已生成的统计日期，避免任务跨过日期边界后与 README 不一致。
    section = readme.split("<summary><strong>今日更新</strong>", 1)[1].split("</details>", 1)[0]
    match = re.search(r"统计日期：(\d{4}-\d{2}-\d{2})（UTC）", section)
    # README 在没有更新时不会输出统计日期。
    if not match:
        if "暂无更新。" in section:
            return "HAP 今日更新", "今日暂无更新。"
        raise ValueError("README 今日更新缺少统计日期")
    date = match.group(1)
    updates = sorted(
        (p for p in projects if ((p.get("latest_hap_release") or {}).get("published_at") or "")[:10] == date),
        key=lambda p: p["latest_hap_release"]["published_at"], reverse=True,
    )
    lines = [f"统计日期：{date}（UTC）", f"共 {len(updates)} 个项目更新", ""]
    for project in updates:
        release = project["latest_hap_release"]
        lines.extend([
            f"{project['full_name']} · {release.get('tag_name', '')}",
            project.get("description") or "",
            release.get("html_url") or project.get("html_url") or "",
            "",
        ])
    return f"HAP 今日更新 · {date}", "\n".join(lines).strip()


def send_message(push_url, title, message):
    payload = {"title": title, "msg": message}
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    if repository:
        payload["url"] = f"https://github.com/{repository}#hap-today"
    response = requests.post(push_url, json=payload, timeout=30)
    response.raise_for_status()
    # 接口即使业务失败也返回 HTTP 200，必须检查 JSON 状态码。
    if response.json().get("status") != 200:
        raise ValueError("MeoW 返回非成功业务状态")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="仅预览推送内容，不发送请求")
    args = parser.parse_args()
    title, message = build_message(
        (ROOT / "README.md").read_text(encoding="utf-8"),
        json.loads((ROOT / "data/projects.json").read_text(encoding="utf-8")),
    )
    if args.dry_run:
        print(f"{title}\n\n{message}")
        return
    try:
        send_message(os.environ.get("MEOW_PUSH_URL") or DEFAULT_PUSH_URL, title, message)
    except (requests.RequestException, ValueError):
        # 不输出包含用户推送地址的 requests 异常。
        print("MeoW 推送失败，请检查网络、推送地址及接口状态。", file=sys.stderr)
        raise SystemExit(1)
    print("今日更新推送成功。")


if __name__ == "__main__":
    main()
