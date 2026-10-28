---
name: start-translate
description: |
  Initialize a new StaticHanHua game-localization project: create Projects/{game_name}, link the game directory, write and research a README archive, and initialize an isolated Git repository. Use when the user says "开始翻译", "新建汉化项目", "初始化汉化", "开始汉化 游戏名", "start translating a game", or provides a game directory and asks to begin localization. The skill searches authoritative online sources for the game's basic information and synopsis, records it in README.md with source links, and never tracks the linked game files. Do not use for text extraction, translation, or repacking.
---

# Start Translate（初始化汉化项目）

## Overview

在进行提取或翻译前，于 `Projects/` 下创建新的游戏汉化项目。给定游戏安装目录后，创建项目目录和 `Game/` 链接，建立项目 README，初始化独立 Git 仓库，并检索可信的游戏资料填入 README。

仅初始化工作区；文本提取使用 `game-unpack`，翻译使用 `game-translate`，回填使用 `game-repack`。用户选择启用图片翻译时，翻译前加入 `game-image-extract`，翻译后加入 `game-image-repack`。

## 图片翻译选项

项目开始前询问一次：“本游戏是否加入图片文字汉化（菜单贴图、标题、便条、场景标牌等）？”可给“加入 / 仅常规文本 / 稍后决定”三个选项。这是缺失的工作范围偏好，不是额外安装审批。已有明确选择或 `localization_project.json` 记录时直接沿用，不重复询问。

把选择作为 `--image-translation enabled|disabled|undecided` 传给初始化脚本，落盘到项目根 `localization_project.json` 并写入README。未答复可先完成初始化和独立的常规解包，但不得默认启用图片扫描/翻译；在图片步骤前取得所需答复。用户后来修改偏好时更新记录，不删除已有提取、译文或补丁。

## Workflow

1. **获取并验证游戏目录**：需要游戏安装目录的绝对路径。用户只提供游戏名时，先要求提供安装路径。确认目录存在。

2. **确定项目名**：使用英文小写连字符形式、无空格，匹配 `[a-z0-9-]+`。源目录为中日韩文字时，要求用户提供英文名；否则根据源目录名自动规范化。该名称同时用作项目目录名与 CSV 前缀。

3. **运行初始化脚本**：

   ```powershell
   python "D:\StaticHanHua\.agents\skills\start-translate\scripts\init_game_project.py" "<game_dir>" --name <game-name> --image-translation <enabled|disabled|undecided>
   ```

   脚本会创建 `Projects/<game-name>/`、指向游戏目录的 `Game/` junction、README 模板和排除 `/Game/` 的 `.gitignore`，随后初始化 `main` 分支的独立 Git 仓库并提交初始文件。绝不复制或提交游戏文件。

4. **验证初始化结果**：列出新项目目录，确认 `Game/` 能解析到真实游戏目录，并确认 `git log` 显示初始提交。

5. **检索并填写 README**：
   - 检查 `Game/`，判断引擎与技术线索：Unity 的 `*_Data/`、.NET/Mono 的 `Managed/`、RPG Maker 的 `*.rpgproject` 等；把可确认结果写入「游戏信息」和「技术栈」。
   - 联网检索游戏资料。优先使用官方 Steam/商店页面，其次使用开发商或发行商官网。只采纳来源明确支持的事实：正式名称、暂译中文名、开发商、发行商、发售平台和日期、支持语言。
   - 在「游戏信息」之后新增 `## 游戏简介`，以中文简要转述玩法、题材或剧情。不要大段复制商店文案；在简介旁提供 Markdown 来源链接。商店若明确标示年龄分级或内容提示，也应简要记录。
   - 无法可靠确认的信息保持空白，不能猜测。游戏目录探测结果与网页资料应分别表述，避免把推测当作官方信息。
   - 初始提交完成后，单独提交资料更新：

     ```powershell
     git add README.md
     git commit -m "文档: 补充游戏简介与基本资料"
     ```

6. **交接给 `game-unpack`**：项目初始化完成后，进入第 1 步文本提取。

## Script Reference

`scripts/init_game_project.py <game_dir> [--name <game_name>] [--projects-root <path>]`

| 参数 | 默认值 | 说明 |
|---|---|---|
| `game_dir` | 必填 | 游戏安装目录的绝对路径 |
| `--name` | 从目录名推导 | 英文小写连字符项目名 |
| `--projects-root` | 自动推导 | 项目根目录；默认工作区的 `Projects/` |
| `--image-translation` | undecided | 用户是否接入图片抽取、翻译和合入 |

## Output Structure

```
Projects/<game-name>/
├── .git/            # 独立汉化工程仓库
├── .gitignore       # 包含 /Game/
├── Game/            # junction，指向游戏安装目录
└── README.md        # 游戏档案与汉化记录
```

## Notes

- 脚本不会覆盖已有项目目录；若目录已存在，先检查其中内容与 `Game/` 链接，再补齐缺少的初始化文件，避免覆盖用户资料。
- 网络盘通常无法创建 junction；此时要求将游戏置于本地磁盘，或在具备权限时改用目录符号链接。
- 脚本仅在未配置 Git 身份时设置本地身份 `StaticHanHua <statichanhua@local>`，不会修改用户的全局 Git 配置。
