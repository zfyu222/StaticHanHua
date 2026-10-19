#!/usr/bin/env python3
"""Create a verifiable external-release localization patch ZIP."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sqlite3
import sys
import tempfile
import zipfile
from contextlib import closing
from datetime import date
from decimal import Decimal
from pathlib import Path, PurePosixPath

AI_NOTICE = "本补丁基于 AI 辅助汉化制作，可能存在措辞、生硬表达、漏译或显示问题，请适当降低预期并欢迎反馈。"
RESERVED = {"补丁说明.txt", "PATCH_MANIFEST.json", "SHA256SUMS.txt"}


def next_version(dispatch: Path, game_name: str) -> str:
    """Return 1.0 for the first release, otherwise the highest release + 0.1."""
    pattern = re.compile(rf"^{re.escape(game_name)}_子非鱼汉化(\d+\.\d+)\.zip$")
    versions = []
    if dispatch.is_dir():
        for candidate in dispatch.glob("*.zip"):
            match = pattern.match(candidate.name)
            if match:
                versions.append(Decimal(match.group(1)))
    if not versions:
        return "1.0"
    return str((max(versions) + Decimal("0.1")).quantize(Decimal("0.0")))


def previous_changelog(dispatch: Path, game_name: str, version: str) -> str:
    """Read the accumulated changelog from the newest earlier release ZIP."""
    pattern = re.compile(rf"^{re.escape(game_name)}_子非鱼汉化(\d+\.\d+)\.zip$")
    earlier = []
    for candidate in dispatch.glob("*.zip") if dispatch.is_dir() else []:
        match = pattern.match(candidate.name)
        if match and Decimal(match.group(1)) < Decimal(version):
            earlier.append((Decimal(match.group(1)), candidate))
    if not earlier:
        return ""
    previous_version, archive_path = max(earlier)
    try:
        with zipfile.ZipFile(archive_path) as archive:
            guide = archive.read("补丁说明.txt").decode("utf-8-sig").replace("\r\n", "\n")
            try:
                manifest = json.loads(archive.read("PATCH_MANIFEST.json").decode("utf-8"))
                previous_date = manifest.get("created", "日期未知")
            except (KeyError, UnicodeDecodeError, json.JSONDecodeError):
                date_match = re.search(r"^发布日期：([^\n]+)$", guide, re.MULTILINE)
                previous_date = date_match.group(1).strip() if date_match else "日期未知"
    except (KeyError, OSError, UnicodeDecodeError, zipfile.BadZipFile):
        return ""
    marker = "\n更新日志\n"
    if marker in guide:
        changelog = guide.split(marker, 1)[1].strip()
        return f"{changelog}\n" if changelog else ""
    # Releases created by the older packager predate the changelog section.
    # Preserve them as the initial release instead of silently dropping history.
    return f"- v{previous_version}（{previous_date}）：首次发布。\n"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_include(value: str, project: Path) -> tuple[Path, PurePosixPath]:
    if "=" not in value:
        raise ValueError("--include 必须使用 source=destination 格式")
    source_text, dest_text = value.split("=", 1)
    source = Path(source_text).expanduser()
    source = (project / source if not source.is_absolute() else source).resolve()
    if not source.is_file():
        raise ValueError(f"找不到要打包的文件：{source}")
    destination = PurePosixPath(dest_text.replace("\\", "/"))
    if destination.is_absolute() or ".." in destination.parts or destination.name in ("", "."):
        raise ValueError(f"不安全的游戏根目录相对路径：{dest_text}")
    if destination.name in RESERVED:
        raise ValueError(f"保留文件名不可作为补丁载荷：{destination.name}")
    return source, destination


def make_guide(args: argparse.Namespace, version: str, changelog: str) -> str:
    """Return a player-facing release note instead of a metadata form."""
    sections = [
        ("适用版本", f"{args.game_version}\n{args.platform}"),
        ("汉化内容", args.coverage),
        ("安装方法", args.install),
        ("卸载 / 恢复", args.uninstall or "删除本补丁新增文件，并用备份恢复被覆盖的原文件。"),
        ("实机验证", args.verification or "未提供；请在安装后自行验证。"),
        ("说明", AI_NOTICE),
        ("后续更新", "补丁后续可能会更新，建议关注或订阅本项目的 GitHub 仓库 / Releases，及时获取新版补丁。"),
        ("已知问题", args.known_issues or "暂无已知问题。"),
        ("文件校验", "请使用压缩包内 `SHA256SUMS.txt` 核对补丁文件完整性；`PATCH_MANIFEST.json` 记录文件路径与哈希值。"),
        ("反馈", "如遇问题，请附上游戏版本、平台、安装步骤、报错截图和日志。请勿在补丁中传播游戏本体或其他受版权保护的原始资源。"),
    ]
    body = "\n\n".join(f"{heading}\n{content}" for heading, content in sections)
    return f"{args.title}\n{'=' * len(args.title)}\n\n{args.introduction}\n\n发布日期：{args.release_date}\n\n{body}\n\n更新日志\n{changelog}- v{version}（{args.release_date}）：{args.changes}\n"


def check_custom_quality(project: Path, entries: list, game_version: str, platform: str) -> None:
    """Gate only initialized custom runs; historical/AINiee releases keep their path."""
    database = project / 'TranslateFiles/state/workflow.sqlite3'
    config = project / 'TranslateFiles/config/workflow.json'
    if not database.is_file():
        return
    if not config.is_file():
        raise ValueError('翻译数据库存在但配置缺失，无法核对质量验收')
    with closing(sqlite3.connect(database)) as connection:
        stored = connection.execute('SELECT value FROM meta WHERE key="config"').fetchone()
    if not stored:
        raise ValueError('翻译数据库配置缺失，无法核对质量验收')
    if json.loads(stored[0]).get('workflow') != 'custom':
        return
    tool_dir = Path(__file__).resolve().parents[4] / 'Tools/localization'
    sys.path.insert(0, str(tool_dir))
    from translation_workflow import Workflow
    check = Workflow(project).release_check()
    if game_version != check['game_version'] or platform != check['platform']:
        raise ValueError('发布版本/平台与实机检查记录不一致')
    tested = {(e['game_path'], e['sha256']) for e in check['payload_files']}
    selected = {(str(destination), sha256(source)) for source, destination in entries}
    if selected != tested or len(entries) != len(check['payload_files']):
        raise ValueError('发布载荷或安装路径与实机验证版本不一致；请重新验证当前载荷')


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, help="游戏项目目录（含 README.md）")
    parser.add_argument("--game-name", help="用于补丁文件名与默认标题的游戏名；默认项目目录名")
    parser.add_argument("--version", help="发布版本号；省略时根据 Dispatch 内既有补丁自动递增 0.1")
    parser.add_argument("--title", help="补丁标题；省略时自动生成")
    parser.add_argument("--introduction", required=True)
    parser.add_argument("--coverage", required=True)
    parser.add_argument("--game-version", required=True)
    parser.add_argument("--platform", required=True)
    parser.add_argument("--release-date", default=date.today().isoformat())
    parser.add_argument("--install", required=True)
    parser.add_argument("--uninstall")
    parser.add_argument("--verification")
    parser.add_argument("--known-issues")
    parser.add_argument("--changes", help="本版更新内容；首次发布默认写为“首次发布”。")
    parser.add_argument("--include", action="append", required=True, metavar="SOURCE=DESTINATION")
    parser.add_argument("--force", action="store_true", help="允许覆盖同名 ZIP")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    project = Path(args.project).expanduser().resolve()
    if not project.is_dir() or not (project / "README.md").is_file():
        parser.error("--project 必须是包含 README.md 的游戏项目目录")
    game_name = args.game_name or project.name
    if not game_name or any(char in game_name for char in '<>:"/\\|?*'):
        parser.error("--game-name 无效，不能包含 Windows 文件名非法字符")
    dispatch = project / "Dispatch"
    if args.version and not re.fullmatch(r"\d+\.\d+", args.version):
        parser.error("--version 必须是 x.y 格式，例如 1.0")
    version = args.version or next_version(dispatch, game_name)
    old_changelog = previous_changelog(dispatch, game_name, version)
    if old_changelog and not args.changes:
        parser.error("升级补丁必须通过 --changes 说明本版更新内容")
    args.changes = args.changes or "首次发布。"
    args.title = args.title or f"《{game_name}》子非鱼AI汉化补丁 v{version}"
    if "子非鱼AI汉化" not in args.title or version not in args.title:
        parser.error("补丁标题必须包含“子非鱼AI汉化”和当前版本号")
    output = dispatch / f"{game_name}_子非鱼汉化{version}.zip"
    entries = [parse_include(item, project) for item in args.include]
    destinations = [str(dest) for _, dest in entries]
    if len(destinations) != len(set(destinations)):
        parser.error("补丁载荷中有重复目标路径")
    if output.exists() and not args.force:
        parser.error(f"补丁已存在：{output}；如需替换请使用 --force")
    if args.dry_run:
        print(f"将生成：{output}")
        for source, dest in entries:
            print(f"  {source} -> {dest}")
        return 0

    try:
        check_custom_quality(project, entries, args.game_version, args.platform)
    except (ValueError, OSError, sqlite3.Error) as error:
        parser.error(str(error))

    output.parent.mkdir(parents=True, exist_ok=True)
    payload_root = dispatch / "Files"
    payload_root.mkdir(parents=True, exist_ok=True)
    files = []
    for source, relative in entries:
        staged = payload_root.joinpath(*relative.parts)
        staged.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, staged)
        files.append({"path": str(relative), "sha256": sha256(staged), "size": staged.stat().st_size})
    with tempfile.TemporaryDirectory(prefix="release-patch-") as temp_dir:
        stage = Path(temp_dir)
        (stage / "补丁说明.txt").write_text(make_guide(args, version, old_changelog), encoding="utf-8-sig")
        manifest = {"archive": output.name, "created": args.release_date, "files": files}
        (stage / "PATCH_MANIFEST.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        sums = "\n".join(f"{item['sha256']}  {item['path']}" for item in files) + "\n"
        (stage / "SHA256SUMS.txt").write_text(sums, encoding="utf-8")
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for _, relative in entries:
                archive.write(payload_root.joinpath(*relative.parts), str(relative))
            for path in sorted(stage.iterdir()):
                archive.write(path, path.name)
    print(f"已生成补丁：{output}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ValueError as error:
        print(f"错误：{error}", file=sys.stderr)
        raise SystemExit(2)
