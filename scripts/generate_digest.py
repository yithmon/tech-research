#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Weekly Digest Generator for Tech Blog Subscription Hub
=====================================================

读取 config/sources.yaml，按 frequency / priority 筛选本周待读来源，
生成一份带「AI 认知科学三命题」映射位的周刊 markdown 到 digests/。

设计原则
--------
- 周刊是「脚手架」：脚本只负责把本周该读谁、在三轴上该看什么位自动排好，
  真实批注由 owner 每周读完手填（对应 frameworks 里的「我的批注」占位）。
- 零外部依赖：优先用 PyYAML；不可用时回退内置的极简 YAML 子集解析器，
  保证在本地和 GitHub Actions(ubuntu-latest) 都能直接跑。

用法
----
    python scripts/generate_digest.py                 # 生成最近一个已结束的周
    python scripts/generate_digest.py --week 2026-W29 # 指定周
    python scripts/generate_digest.py --dry-run       # 只打印，不写文件
    python scripts/generate_digest.py --root /path    # 指定仓库根(默认脚本上级)
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import re
import sys

# ---------------------------------------------------------------------------
# 框架常量（与 frameworks/ai-cognitive-science-three-propositions.md 对齐）
# ---------------------------------------------------------------------------
FRAMEWORK_NAME = "AI 认知科学三命题坐标系"
PROPOSITIONS = [
    ("命题一", "Jagged Intelligence —— 测量对象长什么样（锯齿状，旧尺子量不了）"),
    ("命题二", "Intention Layer —— 人类守在意图层，不是执行层"),
    ("命题三", "Metacognition —— 边界会不会移动，取决于判断力能否被拿走"),
]

# 频率 → 每周纳入规则
#   daily / weekly : 每周都纳入
#   monthly        : 约每月一次（ISO 周号 % 4 == MONTHLY_WEEK_MOD 时纳入）
MONTHLY_WEEK_MOD = 1

# 频率 → 预计阅读时长（分钟），用于每周配额估算
TIME_BY_FREQUENCY = {
    "daily": 30,
    "weekly": 20,
    "monthly": 60,
}


# ---------------------------------------------------------------------------
# YAML 解析：优先 PyYAML，否则用内置子集解析器
# ---------------------------------------------------------------------------
def load_yaml(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        text = fh.read()
    try:
        import yaml  # type: ignore
        return yaml.safe_load(text)
    except Exception:
        return _parse_simple_yaml(text)


def _strip_comment(line: str) -> str:
    """去掉行尾注释；本仓库的 value 中不含 '#'，可安全剥离。"""
    return re.sub(r"\s+#.*$", "", line).rstrip()


def _parse_simple_yaml(text: str):
    """极简 YAML 子集解析器，支持本仓库 sources.yaml 的结构：
    嵌套 mapping + 序列(list of scalars / list of maps)，2 空格缩进。"""
    nodes = []
    for raw in text.split("\n"):
        if not raw.strip():
            continue
        if raw.lstrip().startswith("#"):
            continue
        stripped = _strip_comment(raw)
        if not stripped.strip():
            continue
        indent = len(stripped) - len(stripped.lstrip(" "))
        nodes.append((indent, stripped.strip()))

    pos = [0]

    def parse_block(min_indent: int):
        # 判断是 sequence 还是 mapping
        if pos[0] < len(nodes):
            _, content = nodes[pos[0]]
            if content.startswith("- "):
                return parse_sequence(min_indent)
        return parse_mapping(min_indent)

    def parse_mapping(min_indent: int) -> dict:
        result: dict = {}
        while pos[0] < len(nodes):
            indent, content = nodes[pos[0]]
            if indent < min_indent:
                break
            if content.startswith("- "):
                break
            if ":" not in content:
                pos[0] += 1
                continue
            key, _, val = content.partition(":")
            key = key.strip()
            val = val.strip()
            pos[0] += 1
            if val == "":
                # 嵌套块
                if pos[0] < len(nodes) and nodes[pos[0]][0] > indent:
                    child_indent = nodes[pos[0]][0]
                    if nodes[pos[0]][1].startswith("- "):
                        result[key] = parse_sequence(child_indent)
                    else:
                        result[key] = parse_mapping(child_indent)
                else:
                    result[key] = None
            else:
                result[key] = _coerce(val)
        return result

    def parse_sequence(min_indent: int) -> list:
        result: list = []
        while pos[0] < len(nodes):
            indent, content = nodes[pos[0]]
            if indent < min_indent:
                break
            if not content.startswith("- "):
                break
            item = content[2:].strip()
            pos[0] += 1
            if ":" in item and not item.startswith(("\"", "'")):
                # list of maps 的第一键：把这一行变成 mapping 的起点
                key, _, val = item.partition(":")
                m: dict = {}
                if val.strip() == "":
                    if pos[0] < len(nodes) and nodes[pos[0]][0] > indent:
                        child_indent = nodes[pos[0]][0]
                        if nodes[pos[0]][1].startswith("- "):
                            m[key.strip()] = parse_sequence(child_indent)
                        else:
                            m[key.strip()] = parse_mapping(child_indent)
                    else:
                        m[key.strip()] = None
                else:
                    m[key.strip()] = _coerce(val.strip())
                # 继续吃同缩进的后续键
                while pos[0] < len(nodes):
                    nxt_indent, nxt_content = nodes[pos[0]]
                    if nxt_indent <= indent or nxt_content.startswith("- "):
                        break
                    if ":" not in nxt_content:
                        pos[0] += 1
                        continue
                    k2, _, v2 = nxt_content.partition(":")
                    k2 = k2.strip()
                    v2 = v2.strip()
                    pos[0] += 1
                    if v2 == "":
                        if pos[0] < len(nodes) and nodes[pos[0]][0] > nxt_indent:
                            cind = nodes[pos[0]][0]
                            if nodes[pos[0]][1].startswith("- "):
                                m[k2] = parse_sequence(cind)
                            else:
                                m[k2] = parse_mapping(cind)
                        else:
                            m[k2] = None
                    else:
                        m[k2] = _coerce(v2)
                result.append(m)
            else:
                result.append(_coerce(item))
        return result

    return parse_block(0)


def _coerce(val: str):
    if val == "":
        return None
    if (val.startswith('"') and val.endswith('"')) or (
        val.startswith("'") and val.endswith("'")
    ):
        return val[1:-1]
    low = val.lower()
    if low in ("true", "yes"):
        return True
    if low in ("false", "no"):
        return False
    if low in ("null", "~", "none"):
        return None
    return val


# ---------------------------------------------------------------------------
# 周计算
# ---------------------------------------------------------------------------
def iso_week_range(year: int, week: int):
    """返回该 ISO 周的周一 00:00 与周日 23:59(datetime)。"""
    monday = dt.datetime.strptime(f"{year}-W{week:02d}-1", "%G-W%V-%u")
    sunday = monday + dt.timedelta(days=6)
    return monday, sunday


def digest_window(year: int, week: int):
    """返回 digest 覆盖窗口：上周五 00:00 至本周四 23:59。
    即：该 ISO 周周一往前推 3 天（上周五）→ 该 ISO 周周一往后推 3 天（本周四）。
    共 7 天，锚点为周五生成。"""
    monday = dt.datetime.strptime(f"{year}-W{week:02d}-1", "%G-W%V-%u")
    prev_friday = monday - dt.timedelta(days=3)  # 上周五
    this_thursday = monday + dt.timedelta(days=3)  # 本周四
    return prev_friday, this_thursday


def latest_completed_week(today: dt.date | None = None) -> tuple[int, int]:
    """最近一个「已结束」的周：取包含上一个周五的 ISO 周。
    若今天已过周五，则上周结束；否则再往前推一周。"""
    today = today or dt.date.today()
    # 找最近的周五（含今天往前）
    days_since_friday = (today.weekday() - 4) % 7  # Fri=4
    last_friday = today - dt.timedelta(days=days_since_friday)
    # 该周五所在 ISO 周
    y, w, _ = last_friday.isocalendar()
    return y, w


def parse_week_arg(s: str) -> tuple[int, int]:
    m = re.match(r"^(\d{4})-W(\d{1,2})$", s.strip())
    if not m:
        raise SystemExit(f"周格式错误，应为 YYYY-Www，收到: {s!r}")
    return int(m.group(1)), int(m.group(2))


# ---------------------------------------------------------------------------
# 来源筛选
# ---------------------------------------------------------------------------
def included_this_week(source: dict, iso_week: int) -> bool:
    freq = (source.get("frequency") or "weekly").lower()
    if freq in ("daily", "weekly"):
        return True
    if freq == "monthly":
        return (iso_week % 4) == MONTHLY_WEEK_MOD
    return True


def collect_sources(cfg: dict) -> list[dict]:
    out = []
    for lang in ("english", "chinese"):
        for s in (cfg.get("sources", {}).get(lang) or []):
            s = dict(s)
            s["_lang"] = "EN" if lang == "english" else "CN"
            out.append(s)
    return out


def collect_radar(cfg: dict) -> list[dict]:
    """读取顶层 radar 分组（早期预警源），不计入深读配额。"""
    return [dict(s) for s in (cfg.get("radar") or [])]


def sort_key(s: dict):
    prio = s.get("priority")
    try:
        prio = int(prio)
    except (TypeError, ValueError):
        prio = 99
    return prio


# ---------------------------------------------------------------------------
# 渲染
# ---------------------------------------------------------------------------
def render_digest(year: int, week: int, sources: list[dict], radar: list[dict], generated: dt.date) -> str:
    win_start, win_end = digest_window(year, week)
    start_str = win_start.strftime("%Y-%m-%d")
    end_str = win_end.strftime("%Y-%m-%d")
    fri = win_end + dt.timedelta(days=1)  # 生成锚点：窗口结束次日（周五）
    fri_str = fri.strftime("%Y-%m-%d")

    inc = [s for s in sources if included_this_week(s, week)]
    inc.sort(key=sort_key)
    total_min = sum(TIME_BY_FREQUENCY.get((s.get("frequency") or "weekly").lower(), 20)
                    for s in inc)

    L: list[str] = []
    L.append(f"# Weekly Digest {year}-W{week:02d}（{start_str} – {end_str}）")
    L.append("")
    L.append(f"> 生成日期：{generated.isoformat()} ｜ 框架视角：{FRAMEWORK_NAME}")
    L.append(f"> 覆盖窗口：上周五至本周四（生成锚点周五 {fri_str}）")
    L.append(f"> 本周待读来源：{len(inc)} 个 ｜ 预计阅读配额：~{total_min} 分钟")
    L.append(f"> 自动生成脚手架，批注由 owner 每周读完手填（对应 frameworks 的「我的批注」）。")
    L.append("")

    # 1. 本周阅读清单
    L.append("## 1. 本周阅读清单（按优先级）")
    L.append("")
    L.append("| 优先级 | 来源 | 语言 | 频率 | 平台/链接 | 预计时长 |")
    L.append("|--------|------|------|------|-----------|----------|")
    for s in inc:
        url = s.get("url") or s.get("podcast") or s.get("x") or s.get("wechat") or "—"
        if url.startswith("http"):
            url_cell = f"[{s.get('type','')}]({url})"
        else:
            url_cell = f"{s.get('type','')} · {url}"
        L.append(
            f"| {s.get('priority','-')} | {s.get('name','-')} | {s['_lang']} | "
            f"{s.get('frequency','-')} | {url_cell} | "
            f"{TIME_BY_FREQUENCY.get((s.get('frequency') or 'weekly').lower(),20)} min |"
        )
    L.append("")

    # 2. 早期雷达扫描
    L.append("## 2. 早期雷达扫描（待填）")
    L.append("")
    L.append("> 扫描以下预警源，找「新面孔 / 弱信号」。")
    L.append("> 两源触发规则：同一项目/工具/人被 ≥2 个独立信源提及 → 列入第 5 节「本月值得动手试」候选。")
    L.append("")
    L.append("| 雷达源 | 扫描重点 | 本周新发现（名称 / 链接 / 被提及次数） |")
    L.append("|--------|----------|----------------------------------------|")
    for r in radar:
        url = r.get("url") or ""
        name = r.get("name", "-")
        name_cell = f"[{name}]({url})" if url.startswith("http") else name
        L.append(f"| {name_cell} | {r.get('scan_focus', '-')} | （待填） |")
    L.append("")
    L.append("- 雷达 × 订阅源交集（新面孔是否已被订阅源覆盖，或值得新增订阅）：（待填）")
    L.append("- 命中两源触发的候选（升级至第 5 节）：（待填）")
    L.append("")

    # 3. 三命题落点
    L.append("## 3. 三命题坐标 · 本周落点")
    L.append("")
    L.append("> 每周读完，回到这三轴各记一句：本周哪根轴的证据在增厚？哪根还空着？")
    L.append("")
    for label, desc in PROPOSITIONS:
        L.append(f"- **{label}｜{desc}**")
        L.append(f"  - 本周证据 / 反例：（待填）")
    L.append("")

    # 4. 逐源笔记位
    L.append("## 4. 本周笔记（待填）")
    L.append("")
    L.append("> 每读完一篇，定位三轴 + 记批注。模板：")
    L.append("> - 三轴定位：命题X（补了「测量对象形状」/「人类站位」/「边界移动」）")
    L.append("> - 补了哪块：新证据 / 新反例 / 新概念")
    L.append("> - 我的批注：判断、反驳、联想")
    L.append("")
    for s in inc:
        L.append(f"### {s.get('name','-')}（优先级 {s.get('priority','-')}）")
        L.append("")
        focus = s.get("focus")
        if focus:
            L.append(f"- 关注点：{focus}")
        L.append("- 三轴定位：（待填）")
        L.append("- 补了哪块：（待填）")
        L.append("- 我的批注：（待填）")
        L.append("")

    # 5. 跨源信号
    L.append("## 5. 跨源信号（待填）")
    L.append("")
    L.append("- 本周多个来源共同指向的主题：（待填）")
    L.append("- 本月值得动手试（雷达两源触发命中，附链接）：（待填）")
    L.append("- 与上月/上季复盘的对照：（待填）")
    L.append("")

    # 6. 自动化说明
    L.append("## 6. 关于本文件")
    L.append("")
    L.append("- 由 `scripts/generate_digest.py` 自动生成脚手架；每周五 14:00 (Asia/Shanghai) 由 QoderWork 定时任务「Tech Blog Weekly」扫描生成并推送。")
    L.append("- 真实内容（批注、跨源信号）由 owner 每周读完手填。")
    L.append("- 若某周无新内容，保留脚手架即可，下一周继续累积。")
    L.append("")
    L.append(f"*Next digest: {year}-W{(week % 52) + 1:02d}*")
    L.append("")

    return "\n".join(L)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description="Generate weekly digest scaffold.")
    ap.add_argument("--week", help="目标周 YYYY-Www，默认最近一个已结束的周")
    ap.add_argument("--root", help="仓库根目录，默认脚本上级目录")
    ap.add_argument("--dry-run", action="store_true", help="只打印不写文件")
    args = ap.parse_args()

    root = args.root or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cfg_path = os.path.join(root, "config", "sources.yaml")
    if not os.path.exists(cfg_path):
        print(f"[error] 找不到 {cfg_path}", file=sys.stderr)
        return 2

    cfg = load_yaml(cfg_path)
    sources = collect_sources(cfg)
    radar = collect_radar(cfg)

    if args.week:
        year, week = parse_week_arg(args.week)
    else:
        year, week = latest_completed_week()

    generated = dt.date.today()
    content = render_digest(year, week, sources, radar, generated)

    digest_dir = os.path.join(root, "digests")
    os.makedirs(digest_dir, exist_ok=True)
    out_path = os.path.join(digest_dir, f"{year}-W{week:02d}.md")

    if args.dry_run:
        print(content)
        return 0

    if os.path.exists(out_path):
        print(f"[skip] 已存在，跳过（如需覆盖请先删除）: {out_path}")
        return 0

    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(content)
    print(f"[ok] 已生成: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
