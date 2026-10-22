# -*- coding: utf-8 -*-
"""
analyze_placeholders.py — 扫描待翻译原文，识别占位符/格式化字符串/标记符，
输出：
  1. 控制台摘要 + placeholder_report.md（人读报告）
  2. exclusion_list.json（供共用流程配置 exclusions 参考）

用法：
  python analyze_placeholders.py <source_csv> [--out-dir <目录>] [--src-col N]

  source_csv : 第 1 步提取的原文文件（{gamename}_text.csv）
  --out-dir  : 输出目录，默认为 source_csv 所在目录
  --src-col  : 原文所在列（0 起），默认自动检测
"""
import argparse
import csv
import json
import re
import sys
from collections import Counter
from pathlib import Path

CJK = re.compile(r"[一-鿿]")

# 句型花括号（玩家选项文本，如 {Attack.} {Pull him out.}）：
# 文件中出现此类内容时，单词型 {name} 大概率也是选项文本（如 {Attack}），不应进禁翻表
SENTENCE_CURLY = re.compile(r"\{[^{}\n]{0,80}[.\s][^{}\n]{0,80}\}")

# ---- 占位符家族：名称 / 匹配正则 / 说明 ----
FAMILIES = [
    ("square_bracket", re.compile(r"\[[^\[\]\n]{1,60}\]"),
     "方括号标记（变量占位符/BBCode/富文本标签，如 [Player:Name] [i] [color=\"red\"]）"),
    ("fullwidth_bracket", re.compile(r"【[^【】\n]{1,60}】"),
     "全角方括号标记（如 【名字】，部分日式游戏使用）"),
    ("curly_format", re.compile(r"\{\d+(:[^{}\n]*)?\}"),
     ".NET/格式化占位符（如 {0} {1:0.00}）"),
    ("curly_named", re.compile(r"\{[A-Za-z_][A-Za-z0-9_]*(=[^{}\n]*)?\}"),
     "花括号命名变量（如 {name} {color=#ff0000} {w=wt1}）"),
    ("angle_tag", re.compile(r"</?[A-Za-z][^<>\n]{0,50}>"),
     "尖括号标签（HTML/XML/Unity 富文本，如 <b> </color> <Tag:1>）"),
    ("printf", re.compile(r"%(?:\d+\$)?[0-9.+#-]*[sdfoxXeEfgGcr%]"),
     "printf 格式化符（如 %s %d %.1f %1$s %%）"),
    ("dollar_var", re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_]*\}|\$[A-Za-z_][A-Za-z0-9_]*"),
     "美元变量（如 ${name} $player）"),
    ("rpgmaker_code", re.compile(r"\\(?![nrt]\[)[A-Za-z]{1,5}\[[^\]\n]{0,15}\]|\\(?![nrt]<)[A-Za-z]{1,5}<[^>\n]{0,15}>"),
     "RPG Maker 控制码（如 \\C[1] \\N[2] \\FS<12>）"),
    ("escape_seq", re.compile(r"\\[nrt]"),
     "字面转义序列（\\n \\r \\t，两个字符，非真实换行）"),
    ("html_entity", re.compile(r"&(?:amp|lt|gt|quot|apos|nbsp|#\d+);"),
     "HTML 实体（如 &amp; &lt; &#39;）"),
]

# ---- 每个家族对应的禁翻表候选正则（按严格度排序，取能 100% 覆盖样本的第一条） ----
COVERING_REGEX = {
    "square_bracket": [r"\[[^\[\]\s]{1,60}\]", r"\[[^\[\]\n]{1,60}\]"],
    "fullwidth_bracket": [r"【[^【】\n]{1,60}】"],
    "curly_format": [r"\{\d+(:[^{}\n]*)?\}"],
    "curly_named": [r"\{[A-Za-z_][A-Za-z0-9_]*(=[^{}\n]*)?\}"],
    "angle_tag": [r"</?[A-Za-z][^<>\n]{0,50}>"],
    "printf": [r"%(?:\d+\$)?[0-9.+#-]*[sdfoxXeEfgGcr]", r"%(?:\d+\$)?[0-9.+#-]*[sdfoxXeEfgGcr%]"],
    "dollar_var": [r"\$\{[A-Za-z_][A-Za-z0-9_]*\}|\$[A-Za-z_][A-Za-z0-9_]*"],
    "rpgmaker_code": [r"\\(?![nrt]\[)[A-Za-z]{1,5}\[[^\]\n]{0,15}\]|\\(?![nrt]<)[A-Za-z]{1,5}<[^>\n]{0,15}>"],
    "escape_seq": [r"\\[nrt]"],
    "html_entity": [r"&(?:amp|lt|gt|quot|apos|nbsp|#\d+);"],
}


def load_texts(path: Path, src_col, no_header=False):
    with open(path, "rb") as f:
        physical = f.read().count(b"\n")
    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))
    if not rows:
        return [], 0, physical

    # 表头探测：首行含典型表头词则跳过
    header_words = {"原文", "译文", "source", "text", "original", "translation", "source_text"}
    start = 1 if not no_header and rows[0] and any(w in c.lower() for c in rows[0] for w in header_words) else 0

    texts = []
    for r in rows[start:]:
        if not r:
            texts.append("")
        elif src_col is not None:
            texts.append(r[src_col] if len(r) > src_col else "")
        else:
            texts.append(r[0])
    return texts, len(rows) - start, physical


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("source_csv")
    ap.add_argument("--out-dir", default=None)
    ap.add_argument("--src-col", type=int, default=None)
    ap.add_argument("--no-header", action="store_true", help="正式无表头 CSV，不进行表头猜测")
    args = ap.parse_args()

    src = Path(args.source_csv)
    out_dir = Path(args.out_dir) if args.out_dir else src.parent / "TranslateFiles"
    out_dir.mkdir(parents=True, exist_ok=True)
    texts, logical, physical = load_texts(src, args.src_col, args.no_header)
    if not texts:
        print("[错误] 未读到任何文本行")
        return 1

    print(f"文件: {src.name}  逻辑行={logical} 物理行={physical}")

    # 检测文件是否存在句型花括号（玩家选项文本），存在则 curly_named 降级为人工确认
    has_sentence_curly = any(SENTENCE_CURLY.search(t) for t in texts)

    exclusion = []
    report = ["# 占位符分析报告", "",
              f"- 文件：`{src.name}`", f"- 逻辑行：{logical}，物理行：{physical}", ""]
    if has_sentence_curly:
        report.append("> ⚠ 本文件含 `{...}` 句型花括号（玩家选项文本，需翻译）。"
                      "单词型 `{name}` 匹配已降级为人工确认，未自动加入禁翻表。")
        report.append("")

    any_found = False
    for name, pattern, desc in FAMILIES:
        counter = Counter()
        for t in texts:
            counter.update(pattern.findall(t))
        if not counter:
            continue
        # 句型花括号共存时，curly_named 不自动保护（可能是选项文本）
        demote = name == "curly_named" and has_sentence_curly
        any_found = True
        total = sum(counter.values())
        samples = counter.most_common(12)

        # 选择能覆盖全部样本的禁翻正则
        chosen = None
        for cand in COVERING_REGEX[name]:
            try:
                cre = re.compile(cand)
            except re.error:
                continue
            if all(cre.fullmatch(m) for m in counter):
                chosen = cand
                break
        if chosen is None:
            chosen = COVERING_REGEX[name][-1]

        if demote:
            report.append(f"## {name} — {total} 次出现 ⚠ 需人工确认")
            report.append(f"- {desc}")
            report.append("- 本文件存在句型花括号选项文本，以下匹配**很可能是需翻译的选项**，"
                          "确认是变量后才可加入禁翻表：")
        else:
            exclusion.append({"markers": "", "info": desc.split("（")[0], "regex": chosen})
            report.append(f"## {name} — {total} 次出现")
            report.append(f"- {desc}")
            report.append(f"- 建议禁翻正则：`{chosen}`")
        report.append("- 高频样本：")
        for s, n in samples:
            report.append(f"  - `{s}` ×{n}")
        report.append("")

    # 含 CJK 的标记符警告（原文不应含中文，若有则可能是提取串扰）
    cjk_marks = Counter()
    for t in texts:
        for m in re.findall(r"\[[^\[\]\n]{1,60}\]", t):
            if CJK.search(m):
                cjk_marks[m] += 1
    if cjk_marks:
        report.append("## ⚠ 原文中含中文的方括号内容（疑似可翻译文本，勿加入禁翻表）")
        for s, n in cjk_marks.most_common(10):
            report.append(f"  - `{s}` ×{n}")
        report.append("")

    if not any_found:
        report.append("## 未检测到占位符")
        report.append("原文中未发现常见占位符/格式化字符串，无需配置禁翻表。")

    # 输出
    report_path = out_dir / "placeholder_report.md"
    report_path.write_text("\n".join(report), encoding="utf-8")
    json_path = out_dir / "exclusion_list.json"
    json_path.write_text(json.dumps(exclusion, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"检测到 {len(exclusion)} 类占位符")
    print(f"报告     -> {report_path}")
    print(f"禁翻表   -> {json_path}（结合原文核对后写入共用流程配置 exclusions）")
    for e in exclusion:
        print(f"  - {e['info']}: {e['regex']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
