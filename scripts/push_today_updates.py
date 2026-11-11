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
BUSINESS_ERRORS = {
    400: "标题、消息或分类参数错误",
    403: "内容策略禁止发送",
    404: "昵称未注册，请检查 MEOW_PUSH_URL 中的昵称",
    429: "发送或 IP 频率超限；共享出口 IP 也可能触发限制",
    500: "MeoW 服务器错误",
}


class PushError(ValueError):
    """可以安全写入日志、不包含推送地址或响应原文的错误。"""


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
        (p for p in projects if ((p.get("latest_release") or p.get("latest_hap_release") or {}).get("published_at") or "")[:10] == date),
        key=lambda p: (p.get("latest_release") or p.get("latest_hap_release") or {}).get("published_at") or "", reverse=True,
    )
    lines = [f"统计日期：{date}（UTC）", f"共 {len(updates)} 个项目更新", ""]
    for project in updates:
        release = project.get("latest_release") or project.get("latest_hap_release") or {}
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
    try:
        response = requests.post(push_url, json=payload, timeout=30)
        response.raise_for_status()
    except requests.Timeout:
        raise PushError("请求超时（30 秒），请检查网络及接口状态") from None
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "未知"
        raise PushError(f"HTTP 状态码 {status}，请检查推送地址及接口状态") from None
    except requests.ConnectionError:
        raise PushError("连接失败，请检查 DNS、TLS 及网络连通性") from None
    except requests.RequestException:
        raise PushError("请求失败，请检查推送地址格式及网络配置") from None
    try:
        result = response.json()
    except ValueError:
        raise PushError("接口返回非 JSON 响应，请检查推送地址及接口状态") from None
    if not isinstance(result, dict):
        raise PushError("接口 JSON 响应格式错误，应为对象")
    # 接口即使业务失败也返回 HTTP 200，必须检查 JSON 状态码。
    status = result.get("status")
    if type(status) is not int:
        raise PushError("接口 JSON 响应缺少有效的整数 status 字段")
    if status != 200:
        reason = BUSINESS_ERRORS.get(status, "未知业务错误，请查阅 MeoW 接口文档")
        raise PushError(f"业务状态码 {status}：{reason}")


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
    push_url = os.environ.get("MEOW_PUSH_URL", "").strip()
    if not push_url:
        print("MEOW_PUSH_URL 未设置或为空，使用内置默认推送地址。")
    try:
        send_message(push_url or DEFAULT_PUSH_URL, title, message)
    except PushError as exc:
        print(f"MeoW 推送失败：{exc}。", file=sys.stderr)
        raise SystemExit(1)
    print("今日更新推送成功。")


if __name__ == "__main__":
    main()
