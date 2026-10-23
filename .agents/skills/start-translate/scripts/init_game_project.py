#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
init_game_project.py - 初始化 StaticHanHua 游戏汉化项目工作区

在 Projects/ 下为指定游戏创建汉化工程目录：
  - 创建 Projects/{game_name}/ 文件夹
  - 创建目录联接 Game/ -> 用户提供的游戏目录（无需管理员权限）
  - 生成 README.md（汉化项目档案模板）
  - 写入 .gitignore（排除 /Game/，游戏文件不纳入版本控制）
  - 初始化 git 仓库并提交初始版本

用法:
    python init_game_project.py <game_dir> [--name <game_name>] [--projects-root <path>]

示例:
    python init_game_project.py "D:/Games/SomeGame" --name some-game
    python init_game_project.py "D:/Games/MyGame"
"""

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path


# --------------------------------------------------------------------------
# 编码安全的输出
# --------------------------------------------------------------------------
def safe_print(message):
    """Print safely, handling Unicode issues on Windows consoles."""
    try:
        print(message)
    except UnicodeEncodeError:
        import unicodedata
        clean = message.encode('ascii', 'replace').decode('ascii')
        print(clean)


def _decode_stream(b):
    """Decode subprocess bytes output tolerantly across Windows code pages."""
    if b is None:
        return ''
    for enc in ('utf-8', 'mbcs', 'gbk'):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            continue
    return b.decode('utf-8', errors='replace')


# --------------------------------------------------------------------------
# 路径 / 名称处理
# --------------------------------------------------------------------------
def default_projects_root():
    """Infer <workspace>/Projects by walking up from this script's location."""
    p = Path(__file__).resolve()
    for parent in [p.parent] + list(p.parents):
        if (parent / 'Projects').is_dir():
            return str(parent / 'Projects')
    return r'd:\StaticHanHua\Projects'


def normalize_name(raw):
    """
    Normalize a folder name to lowercase-hyphen-case, ASCII only.
    Returns '' if nothing ASCII remains (e.g. pure CJK name).
    """
    name = os.path.basename(os.path.normpath(raw))
    name = name.lower()
    name = re.sub(r'[\s_]+', '-', name)          # spaces/underscores -> hyphen
    name = re.sub(r'[^a-z0-9-]', '', name)        # keep ASCII alnum + hyphen
    name = re.sub(r'-+', '-', name)               # collapse hyphens
    name = name.strip('-')
    return name


# --------------------------------------------------------------------------
# 模板
# --------------------------------------------------------------------------
README_TEMPLATE = """# {title} 汉化项目

> 本文件由 `start-translate` skill 自动生成，请补充游戏信息与汉化方案。

## 游戏信息
- 原名：
- 中文名：
- 开发商：
- 发行平台：
- 引擎：
- 原语言：

## 汉化目标
- 范围：
- 目标语言：简体中文
- 图片文字翻译：{image_translation}

## 图片文字流程
- 选择记录：`localization_project.json`；未决定时可先提取常规文本，不能默认启用图片步骤。
- 启用时：常规解包 → `game-image-extract` → `game-translate`（常规/图片数据集独立）→ `game-image-repack` → 引擎回填。

## 技术栈
| 环节 | 工具 | 版本 |
|------|------|------|
| 提取 |  |  |
| 翻译 |  |  |
| 回填 |  |  |

## 文本提取
- 文本位置：
- 工具/命令：
- 步骤：
- 注意事项：

## 翻译
- 词典：
- 策略：

## 回填
- 工具/命令：
- 步骤：
- 验证方法：

## 已知问题

## 版本记录
- {init_date} - 初始化汉化项目工作区（start-translate skill）
"""


GITIGNORE_CONTENT = """# 游戏目录（目录联接/junction，指向外部游戏安装目录，不纳入版本控制）
/Game/

# 备份与中间产物
*.bak
*.bak0000
*.tmp
/ImageText/work/images/
/ImageText/work/cache/
/ImageText/work/tiered/qwen/

# Python
__pycache__/
*.pyc

# 系统/编辑器
Thumbs.db
Desktop.ini
.DS_Store
.vscode/
.idea/
"""


# --------------------------------------------------------------------------
# 子操作
# --------------------------------------------------------------------------
def create_junction(link_path, target_path):
    """
    Create a Windows directory junction (mklink /J). Does NOT require admin.
    Returns (success: bool, message: str).
    """
    link_path = str(link_path)
    target_path = str(target_path)
    r = subprocess.run(
        ['cmd', '/c', 'mklink', '/J', link_path, target_path],
        capture_output=True,
    )
    out = _decode_stream(r.stdout) + _decode_stream(r.stderr)
    if r.returncode == 0:
        return True, out.strip()
    return False, out.strip()


def git(args, cwd):
    """Run a git command, return CompletedProcess with decoded text."""
    r = subprocess.run(['git'] + args, cwd=str(cwd), capture_output=True)
    return r


def ensure_git_identity(repo_dir):
    """If no user.name/user.email is configured, set a local default."""
    name_r = git(['config', 'user.name'], repo_dir)
    email_r = git(['config', 'user.email'], repo_dir)
    if not _decode_stream(name_r.stdout).strip():
        git(['config', '--local', 'user.name', 'StaticHanHua'], repo_dir)
    if not _decode_stream(email_r.stdout).strip():
        git(['config', '--local', 'user.email', 'statichanhua@local'], repo_dir)


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------
def init_project(game_dir, game_name, projects_root, image_translation='undecided'):
    if image_translation not in ('enabled','disabled','undecided'):raise ValueError('Invalid image translation choice')
    game_dir_path = Path(game_dir).resolve()
    if not game_dir_path.is_dir():
        safe_print(f"[ERROR] 游戏目录不存在或不是目录: {game_dir_path}")
        return 1

    if not game_name:
        game_name = normalize_name(game_dir_path.name)
    if not game_name:
        safe_print(
            "[ERROR] 无法从目录名推断合法的英文游戏名（目录名可能为纯中文/日文）。\n"
            "       请用 --name 显式指定一个英文、无空格、连字符风格的游戏名，例如：\n"
            "       python init_game_project.py \"<game_dir>\" --name some-game"
        )
        return 1

    # 二次校验规范化后的名称
    norm = normalize_name(game_name)
    if norm != game_name:
        safe_print(f"[WARN] 游戏名已规范化: {game_name!r} -> {norm!r}")
        game_name = norm

    projects_root_path = Path(projects_root).resolve()
    projects_root_path.mkdir(parents=True, exist_ok=True)
    project_dir = projects_root_path / game_name

    if project_dir.exists():
        safe_print(f"[ERROR] 项目目录已存在，拒绝覆盖: {project_dir}")
        safe_print("       如需重新初始化，请先删除或重命名该目录。")
        return 1

    project_dir.mkdir(parents=True, exist_ok=False)
    safe_print(f"[OK] 创建项目目录: {project_dir}")

    # 1) 创建 Game 目录联接
    game_link = project_dir / 'Game'
    ok, msg = create_junction(game_link, game_dir_path)
    if ok:
        safe_print(f"[OK] 创建目录联接 Game/ -> {game_dir_path}")
    else:
        safe_print(f"[ERROR] 创建目录联接失败: {msg}")
        safe_print("       请检查游戏目录路径是否正确。项目目录已创建，Game 联接未建立。")
        return 2

    # 2) 写入 README.md
    readme_path = project_dir / 'README.md'
    from datetime import date
    readme_content = README_TEMPLATE.format(
        title=game_name,
        init_date=date.today().isoformat(),
        image_translation={'enabled':'启用','disabled':'不启用','undecided':'待用户决定'}[image_translation],
    )
    readme_path.write_text(readme_content, encoding='utf-8')
    safe_print(f"[OK] 写入 {readme_path.name}")
    (project_dir/'localization_project.json').write_text(json.dumps({'schema':1,'image_translation':{'status':image_translation,'reason':'User choice at initialization' if image_translation!='undecided' else 'Preference not supplied yet'}},ensure_ascii=False,indent=2),encoding='utf-8')

    # 3) 写入 .gitignore
    gitignore_path = project_dir / '.gitignore'
    gitignore_path.write_text(GITIGNORE_CONTENT, encoding='utf-8')
    safe_print(f"[OK] 写入 {gitignore_path.name}（排除 /Game/）")

    # 4) 初始化 git 仓库
    init_r = git(['init'], project_dir)
    if init_r.returncode != 0:
        safe_print(f"[ERROR] git init 失败: {_decode_stream(init_r.stderr).strip()}")
        safe_print("       项目目录、Game 联接、README.md、.gitignore 已就绪，但 git 仓库未初始化。")
        return 3
    safe_print("[OK] git init")

    # 默认分支改为 main（如 git 版本支持）
    git(['symbolic-ref', 'HEAD', 'refs/heads/main'], project_dir)

    ensure_git_identity(project_dir)

    # 5) 暂存并提交
    add_r = git(['add', 'README.md', '.gitignore', 'localization_project.json'], project_dir)
    if add_r.returncode != 0:
        safe_print(f"[WARN] git add 失败: {_decode_stream(add_r.stderr).strip()}")

    commit_r = git(
        ['commit', '-m', '初始化汉化项目工作区 (start-translate)'],
        project_dir,
    )
    if commit_r.returncode == 0:
        safe_print("[OK] 初始提交完成")
    else:
        err = _decode_stream(commit_r.stderr).strip()
        out = _decode_stream(commit_r.stdout).strip()
        # nothing to commit / already exists 等非致命情况
        if 'nothing to commit' in (err + out).lower():
            safe_print("[WARN] 没有可提交的内容（可能已被提交过）")
        else:
            safe_print(f"[WARN] git commit 返回码非0: {err or out}")

    # 总结
    safe_print("\n========== 完成 ==========")
    safe_print(f"项目目录 : {project_dir}")
    safe_print(f"Game 联接: {game_link} -> {game_dir_path}")
    safe_print(f"README   : {readme_path}")
    safe_print(f"Git 仓库 : {project_dir / '.git'}")
    safe_print("\n下一步:")
    safe_print("  1. 在 README.md 中补充游戏信息（原名、中文名、引擎等）")
    safe_print("  2. 使用 game-unpack skill 提取文本（第 1 步）")
    return 0


def main():
    parser = argparse.ArgumentParser(
        description='初始化 StaticHanHua 游戏汉化项目工作区',
    )
    parser.add_argument('game_dir', help='游戏安装目录的绝对路径')
    parser.add_argument(
        '--name',
        default=None,
        help='英文游戏名（小写、连字符、无空格）。留空则从目录名推断。',
    )
    parser.add_argument(
        '--projects-root',
        default=None,
        help=f'Projects 根目录（默认自动推断，回退到 d:\\StaticHanHua\\Projects）',
    )
    parser.add_argument('--image-translation',choices=['enabled','disabled','undecided'],default='undecided',help='Persist user preference; never defaults to enabling image translation')
    args = parser.parse_args()

    projects_root = args.projects_root or default_projects_root()
    return init_project(args.game_dir, args.name, projects_root, args.image_translation)


if __name__ == '__main__':
    sys.exit(main())
