---
name: game-release-patch
description: Generate an external-release game localization patch ZIP under Projects/{GameName}/Dispatch, including translated files, a Chinese installation guide, manifest, and checksums. Use when the user asks to publish, release, distribute, package for other players, create a patch ZIP, or create/update 补丁说明 for a completed game translation.
---

# Game Release Patch（发布补丁）

Create a self-contained distribution package that lets players install the localization without access to the project workspace. The release artifact is `Projects/{GameName}/Dispatch/{archive}.zip`.

## Workflow

1. Read the project's `README.md` and repack results. Do not publish before the repacked files have been tested in the target game version.
   - For initialized custom translation runs, read `../game-translate/references/quality_workflow.md` and run `Tools/localization/translation_workflow.py --project <project> release-check`. The current CSV, Sol acceptance, tested payload hashes and in-game evidence must pass. Package exactly the tested payload destinations and use the recorded game version/platform; the packager enforces this before creating a ZIP. A dry run is only a preview. Older/AINiee projects without a custom database retain their existing verification workflow.
2. Choose only files the player needs: normally a translation plugin, modified assets, configuration, and any required runtime files. Never include `Game/`, the original executable, paid game data, API keys, developer logs, or source CSVs unless the player explicitly needs them.
3. Preserve the exact destination relative to the game root. For example, a plugin DLL destined for `BepInEx/plugins/` must be packaged under `BepInEx/plugins/`.
4. Use the project directory name as the game name, or pass `--game-name`. The ZIP name is always `{游戏名}_子非鱼汉化{版本号}.zip`. The first release is `1.0`; each later release is the highest existing release plus `0.1`. Its title is automatically `《{游戏名}》子非鱼AI汉化补丁 v{版本号}` and must keep that version number.
5. Draft factual release metadata from the tested build: introduction, coverage, supported game version and store/platform, translation date, installation and uninstall methods, verification result, known issues, and contact/feedback channel if one exists. Write the guide as a player-facing release note: start with the actual patch title, then use natural headings such as “适用版本” and “安装方法”. Never expose form labels such as “补丁标题” / “补丁介绍”, or numbered metadata fields, in the player-facing document.
6. Run `scripts/create_release_patch.py` from the project directory. It first copies every selected payload file to `Dispatch/Files/`, retaining its game-root relative layout; inspect this folder before publishing. The ZIP is created from these `Files/` copies, not the original build paths. Pass every payload file with its game-root destination and use `--dry-run` first for non-trivial patches.
7. On an upgrade, pass `--changes "<本版改动>"`. The skill preserves the previous ZIP's update log and appends the new version entry at the very end of `补丁说明.txt`; never replace earlier entries. The first release automatically records “首次发布”。
8. Inspect the ZIP: it must contain `补丁说明.txt` at its root, the payload directories, `PATCH_MANIFEST.json`, and `SHA256SUMS.txt`. Extract it into a temporary directory and confirm that its layout matches the installation instructions.
9. Publish to the game's **independent public GitHub repository** before the external mirror upload. Confirm the project has its own `origin` remote (never use the `StaticHanHua` workspace repository), and verify `Game/` is ignored and absent from the Git index. Commit the release documentation and small source/text artifacts, but do not commit the ZIP, game files, or large binaries unless Git LFS is deliberately in use. Push the release commit to `origin`.
10. Create or update the GitHub Release for the patch version and upload the newly validated ZIP as its release asset. Use the patch version as the tag (for example `v1.0`), set the release as the latest stable release, and include concise player-facing installation/compatibility notes. Verify the release is public, published (not a draft), and its asset can be downloaded from the Release page. The GitHub Release page is the canonical player download location.
11. Mirror the same ZIP with the `baidu-netdisk-local-uploader` MCP. Call `ensure_directory` with `/Hanhua/{GameName}`, then call `delete_existing_patch_versions` with that directory and `{GameName}`. It deletes only prior files matching `{GameName}_子非鱼汉化<version>.zip`; require `status: success`, record the removed paths, and stop on failure. Finally call `upload_file` with the new local ZIP and `remote_path` `/Hanhua/{GameName}`; require `status: success` and record the returned remote path.
12. Update `README.md` with the archive name, supported build, payload files, verification result, GitHub Release URL, Baidu Netdisk path, and known issues. Commit the final documentation update and push it to the game's independent GitHub repository. Do not commit large ZIPs or game binaries unless the project deliberately uses Git LFS.

## Package command

```powershell
py D:\StaticHanHua\.agents\skills\game-release-patch\scripts\create_release_patch.py `
  --project . `
  --game-name "My Game" `
  --introduction "为 PC 版《My Game》制作的简体中文汉化补丁。" `
  --coverage "主线剧情、常用 UI、系统提示" `
  --game-version "Steam v1.2.3" `
  --platform "Steam（Windows）" `
  --install "退出游戏后，将压缩包内全部文件按原目录覆盖到游戏根目录；同意覆盖时请先备份原文件。" `
  --uninstall "删除本补丁新增文件，并用备份恢复被覆盖的原文件。" `
  --verification "已在 Steam v1.2.3 完成启动、主菜单与第一章对话测试。" `
  --known-issues "部分超长 UI 文本可能换行。" `
  --changes "修复第一章两处漏译，并补充设置菜单。" `
  --include ".\\Release\\BepInEx\\plugins\\MyGameTranslation.dll=BepInEx/plugins/MyGameTranslation.dll" `
  --include ".\\Release\\BepInEx\\config\\MyGameTranslation.cfg=BepInEx/config/MyGameTranslation.cfg"
```

`--include` uses `source=destination`. The destination is relative to the game root and must never contain `..`. Files are copied to `Dispatch/Files/<destination>` before compression. Omit `--version` to automatically use `1.0` for the first release and increment the highest existing release by `0.1` after that. Upgrades require `--changes`; the script appends it to the cumulative update log at the end of the guide. Use `--version 1.2` only to deliberately issue that version.

## Required guide contents and style

The generated `补丁说明.txt` must contain:

1. 补丁标题
2. 补丁介绍
3. 汉化覆盖内容
4. 支持的游戏版本与游戏平台
5. 汉化日期
6. AI 汉化说明（固定提醒玩家适当降低预期）
7. 安装方法
8. 卸载/恢复方法
9. 验证结果与已知问题
10. 文件校验、反馈方式和发布注意事项；包含后续更新提醒：补丁后续可能会更新，建议关注或订阅本项目的 GitHub 仓库 / Releases，或收藏、订阅网盘发布渠道，及时获取新版补丁。已有公开链接时附上对应链接，不编造地址。GitHub Release 正文也需包含此提醒。
11. 更新日志（位于说明书末尾，按版本持续追加）

Use the actual patch title as the first line, followed by a simple separator. Present the remaining information in natural, player-readable sections rather than as a field list; for example: “适用版本”, “汉化内容”, “安装方法”, “卸载 / 恢复”, “实机验证”, “说明”, “已知问题”, “文件校验”, and “反馈”. Do not include headings such as “补丁标题” or “补丁介绍”, and do not prefix sections with `1、`, `2、` etc.

Keep instructions specific: state whether to copy, merge, or replace files; whether a backup is mandatory; whether a mod loader is bundled; and where players can report issues. Never imply compatibility that has not been tested.

## Release checks

- Keep `补丁说明.txt` in the ZIP root and encode it as UTF-8 with BOM for Windows Notepad compatibility.
- Confirm every payload path is exactly the path named in the installation method.
- Confirm SHA-256 entries match the ZIP payload after extraction.
- Test on a clean copy or restore from backup, then install exactly as the guide states.
- Keep the required ZIP name, `{游戏名}_子非鱼汉化{版本号}.zip`, e.g. `My Game_子非鱼汉化1.0.zip`; retain old releases instead of overwriting them.

## README and Git record

Append a release entry such as:

```markdown
## 对外发布
- 补丁：`Dispatch/My Game_子非鱼汉化1.0.zip`
- 支持版本：Steam v1.2.3（Windows）
- 内容：BepInEx 翻译插件与配置文件
- 验证：全新副本按说明安装后，主菜单与第一章文本正常显示。
- 已知问题：部分超长 UI 文本可能换行。
- GitHub Release：`https://github.com/<owner>/<game-repo>/releases/tag/v1.0`
- 百度网盘：`/Hanhua/My Game/My Game_子非鱼汉化1.0.zip`
```

Then commit the documentation:

```powershell
git add README.md
git commit -m "发布: 生成 v1.0 对外汉化补丁"
```
