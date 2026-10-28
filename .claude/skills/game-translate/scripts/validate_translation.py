# -*- coding: utf-8 -*-
"""
validate_translation.py — 译文完整性校验：检测 AI 翻译对占位符/格式化字符串/
标记符/换行结构造成的破坏，可选自动修复。

检测项：
  1. 逻辑行数是否一致（错位即全盘报废）
  2. 物理行数膨胀（字段内被 AI 写入真实换行符）
  3. 每行受保护标记的多重集合是否一致（方括号/花括号变量/尖括号/printf/美元变量）
  4. 标记是否被翻译成中文
  5. 半角 [] 是否被转成全角 【】
  6. 字面转义 \\n 数量是否一致
  7. 变量名是否被改写（如 [Upset:Name] -> [Name]）

用法：
  python validate_translation.py <source_csv> <translated_csv> [--fix] [--fix-newlines]
                                 [--src-col N] [--dst-col N] [--retranslate-out 文件名]

  无参数运行时只检测不修改；--fix 对标记数量一致的行按族对位还原（自动备份 .bak）；
  --fix-newlines 把字段内真实换行还原为字面 \\n 两字符。
退出码：0=无问题或已全部修复，1=存在需人工/重翻的行，2=行数不一致等致命错误。
"""
import argparse
import csv
import re
import shutil
import sys
from pathlib import Path

CJK = re.compile(r"[一-鿿]")

# 受保护标记家族（译文中这些内容必须与原文多重集合一致）
FAMILIES = {
    # 方括号：源端仅半角；译端半角+全角（全角视为半角被转换）
    "bracket": (re.compile(r"\[[^\[\]\n]{1,60}\]"),
                re.compile(r"\[[^\[\]\n]{1,60}\]|【[^【】\n]{1,60}】")),
    "curly_format": (re.compile(r"\{\d+(:[^{}\n]*)?\}"),
                     re.compile(r"\{\d+(:[^{}\n]*)?\}")),
    "curly_named": (re.compile(r"\{[A-Za-z_][A-Za-z0-9_]*(=[^{}\n]*)?\}"),
                    re.compile(r"\{[A-Za-z_][A-Za-z0-9_]*(=[^{}\n]*)?\}")),
    "angle": (re.compile(r"</?[A-Za-z][^<>\n]{0,50}>"),
              re.compile(r"</?[A-Za-z][^<>\n]{0,50}>")),
    "printf": (re.compile(r"%(?:\d+\$)?[0-9.+#-]*[sdfoxXeEfgGcr%]"),
               re.compile(r"%(?:\d+\$)?[0-9.+#-]*[sdfoxXeEfgGcr%]")),
    "dollar": (re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_]*\}|\$[A-Za-z_][A-Za-z0-9_]*"),
               re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_]*\}|\$[A-Za-z_][A-Za-z0-9_]*")),
}

LITERAL_N = re.compile(r"\\n")
HEADER_WORDS = {"原文", "译文", "source", "text", "original", "translation", "source_text"}
# 句型花括号（玩家选项文本）：存在时 curly_named 家族停用，避免误伤选项翻译
SENTENCE_CURLY = re.compile(r"\{[^{}\n]{0,80}[.\s][^{}\n]{0,80}\}")


def load_csv(path: Path):
    with open(path, "rb") as f:
        physical = f.read().count(b"\n")
    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))
    return rows, physical


def pick_col(rows, explicit, prefer_second):
    """选择文本列：显式指定 > 自动（表头含译文词时按多数非空列判定）。"""
    if explicit is not None:
        return explicit, 0
    start = 0
    if rows and any(w in c.lower() for c in rows[0] for w in HEADER_WORDS):
        start = 1
    if prefer_second:
        data = rows[start:]
        if data:
            filled = sum(1 for r in data if len(r) > 1 and r[1].strip())
            if filled > len(data) / 2:
                return 1, start
    return 0, start


def is_corrupted(tag: str) -> bool:
    return bool(CJK.search(tag)) or tag.startswith("【")


def restore_family(src_text, dst_text, src_pat, dst_pat):
    """同族标记数量一致且存在损坏标记时按序对位还原；返回 (新文本, 是否修复)。

    注意：仅当译文标记含中文或被全角化时才修复。译文与原文标记顺序不同
    （AI 为适配中文语法调整语序）属于合法行为，不做还原——否则会把变量对错位置。
    """
    s_tags = src_pat.findall(src_text)
    d_spans = list(dst_pat.finditer(dst_text))
    if not s_tags or len(s_tags) != len(d_spans):
        return dst_text, False
    if not any(is_corrupted(m.group(0)) for m in d_spans):
        return dst_text, False
    pieces, pos = [], 0
    for m, tag in zip(d_spans, s_tags):
        pieces.append(dst_text[pos:m.start()])
        pieces.append(tag)
        pos = m.end()
    pieces.append(dst_text[pos:])
    return "".join(pieces), True


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("source_csv")
    ap.add_argument("translated_csv")
    ap.add_argument("--fix", action="store_true", help="对位还原数量一致的损坏标记")
    ap.add_argument("--fix-newlines", action="store_true", help="真实换行还原为字面 \\n")
    ap.add_argument("--src-col", type=int, default=None)
    ap.add_argument("--dst-col", type=int, default=None)
    ap.add_argument("--retranslate-out", default="need_retranslate.csv")
    ap.add_argument("--report-dir", default=None, help="默认译文所在项目的 TranslateFiles")
    ap.add_argument("--backup-dir", default=None, help="默认 TranslateFiles/backups")
    args = ap.parse_args()

    src_path, dst_path = Path(args.source_csv), Path(args.translated_csv)
    report_dir = Path(args.report_dir) if args.report_dir else dst_path.parent / "TranslateFiles"
    backup_dir = Path(args.backup_dir) if args.backup_dir else report_dir / "backups"
    src_rows, src_physical = load_csv(src_path)
    dst_rows, dst_physical = load_csv(dst_path)

    s_col, s_start = pick_col(src_rows, args.src_col, prefer_second=False)
    d_col, d_start = pick_col(dst_rows, args.dst_col, prefer_second=True)
    src = [r[s_col] if len(r) > s_col else "" for r in src_rows[s_start:]]
    dst = [r[d_col] if len(r) > d_col else "" for r in dst_rows[d_start:]]

    print(f"原文 {src_path.name}: 逻辑行={len(src)} 物理行={src_physical}")
    print(f"译文 {dst_path.name}: 逻辑行={len(dst)} 物理行={dst_physical}")

    # 致命：逻辑行数不一致
    if len(src) != len(dst):
        print(f"[致命] 逻辑行数不一致（{len(src)} vs {len(dst)}），无法逐行对比，请先修复对齐")
        return 2

    embedded_nl = [i + 1 + d_start for i, d in enumerate(dst) if "\n" in d or "\r" in d]
    if embedded_nl:
        print(f"[警告] {len(embedded_nl)} 行字段内含真实换行符（物理行膨胀），行号: "
              f"{embedded_nl[:10]}{'...' if len(embedded_nl) > 10 else ''}")

    # 句型花括号门控：原文含选项文本时停用 curly_named，防止把 {Attack} 类选项误判为变量
    families = dict(FAMILIES)
    if any(SENTENCE_CURLY.search(s) for s in src):
        families.pop("curly_named", None)
        print("[提示] 原文含句型花括号（选项文本），已停用 {name} 变量校验")

    fixed, extra, retrans = [], [], []
    literal_n_bad = []

    for i, (s, d) in enumerate(zip(src, dst)):
        lineno = i + 1 + d_start
        if LITERAL_N.findall(s) != LITERAL_N.findall(d):
            literal_n_bad.append(lineno)

        line_fixed = False
        needs_retrans = False
        d_new = d
        for src_pat, dst_pat in families.values():
            d_new, did = restore_family(s, d_new, src_pat, dst_pat)
            line_fixed = line_fixed or did

        # 修复后再判定
        mismatch_families = []
        for name, (src_pat, dst_pat) in families.items():
            st = sorted(src_pat.findall(s))
            dt = sorted(dst_pat.findall(d_new))
            if st != dt:
                corrupted = [t for t in dt if is_corrupted(t)]
                missing = [t for t in st if st.count(t) > dt.count(t)]
                if not corrupted and len(dt) > len(st) and not missing:
                    extra.append((lineno, name))
                else:
                    mismatch_families.append(name)
                    needs_retrans = True
        if needs_retrans:
            retrans.append((lineno, s, d_new))
        elif line_fixed:
            fixed.append(lineno)
        dst[i] = d_new

    # 汇总
    print("=" * 60)
    cjk_tag_lines = sorted({ln for ln, s, d in retrans})
    print(f"可对位还原的标记修复: {len(fixed)} 行")
    print(f"标记被改/丢/译，需重翻: {len(retrans)} 行")
    print(f"AI 多加合法标记(信息) : {len(extra)} 行")
    if literal_n_bad:
        print(f"字面 \\n 数量不一致  : {len(literal_n_bad)} 行 -> {literal_n_bad[:10]}")

    wrote_fix = False
    if (args.fix or args.fix_newlines) and (fixed or (args.fix_newlines and embedded_nl)):
        backup_dir.mkdir(parents=True, exist_ok=True)
        bak = backup_dir / (dst_path.name + ".bak")
        if not bak.exists():
            shutil.copy2(dst_path, bak)
            print(f"已备份 -> {bak.name}")
        if args.fix_newlines:
            for i, d in enumerate(dst):
                if "\n" in d or "\r" in d:
                    d2 = d.replace("\r\n", "\n").replace("\r", "\n")
                    d2 = d2.replace("  \n", "\\n").replace(" \n", "\\n").replace("\n", "\\n")
                    dst[i] = d2
        for i, text in enumerate(dst):
            row = dst_rows[i + d_start]
            while len(row) <= d_col:
                row.append("")
            row[d_col] = text
        with open(dst_path, "w", encoding="utf-8-sig", newline="") as f:
            csv.writer(f).writerows(dst_rows)
        wrote_fix = True
        print(f"已写回修复 -> {dst_path.name}")
    elif fixed and not args.fix:
        print("提示：加 --fix 可自动应用对位还原修复")

    if retrans:
        report_dir.mkdir(parents=True, exist_ok=True)
        out = report_dir / args.retranslate_out
        with open(out, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["行号", "原文", "当前译文"])
            w.writerows(retrans)
        print(f"待重翻清单 -> {out.name}")

    if retrans or literal_n_bad:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
