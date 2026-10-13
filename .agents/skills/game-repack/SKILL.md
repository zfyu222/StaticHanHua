---
name: game-repack
description: |
  This skill should be used when the user needs to repack translated text back into game files as Step 3 of the StaticHanHua 3-step localization pipeline. It covers Unity AssetBundle text replacement, .NET/IL2CPP DLL patching and BepInEx injection, RPG Maker data file replacement, and native binary patching. Trigger when the user says "打包", "回填", "repack", "inject translation", or "put translations back into game". The primary input is {gamename}_text_translated.csv (one translated text per line, aligned with the original extraction).
---

# Game Repack (打包) — 翻译文本回填

## Overview

Repack translated text back into game files as Step 3 of the 3-step StaticHanHua pipeline.

- **Input**: `Projects/{GameName}/{gamename}_text_translated.csv` (one translated text per line)
- **Alignment**: Each line in `_translated.csv` corresponds to the same line in `_text.csv` from Step 1.
- **Output**: Modified game files, ready for testing.

## Workflow

1. **Read `README.md`** at `Projects/{GameName}/README.md` — it contains:
   - Game engine and version
   - Extraction tech stack (what was done in Step 1)
   - Translation notes (font choices, terminology, special context — written during Step 2)
2. **Load the translated file** — `{gamename}_text_translated.csv`.
3. **Identify the repack method** based on the game engine and extraction method documented in README.
4. **Execute repack** following the engine-specific guide below.
5. **Test** — launch the game and verify translations appear correctly.
6. **Update README.md** — write repack tech stack, verification results, known issues into the 回填 / 已知问题 sections.
7. **Commit to git** — in the project directory, stage README (+ repack artifacts if reasonably sized) and commit (see `CODEBUDDY.md` → Git 与 README 协作规范):
   ```powershell
   cd Projects/{GameName}
   git add README.md
   git commit -m "回填: <brief>"
   ```
   > `Game/` is never committed; large binary repack outputs may be excluded or tracked via Git LFS.

## Input Format

`{gamename}_text_translated.csv` — one translated text per line, aligned with the original `{gamename}_text.csv`:

```
早上好
再见
今天天气真好
谢谢
```

> Line 1 of `_translated.csv` is the translation of line 1 of `_text.csv`, and so on.

## Engine-Specific Repack

### Unity (AssetBundle / .assets)

**Tool**: UABEAvalonia CLI (`Tools/UABEA/UABEAvalonia/bin/Release/net8.0/UABEAvalonia.exe`).

**Step 1 — Map translations to TextAssets**:
- Refer to README.md's extraction section for TextAsset names and pathIds.
- Split `{gamename}_text_translated.csv` into per-asset translation files, matching the original export order.

**Step 2 — Import translated text**:
```powershell
.\UABEAvalonia.exe importtext "resources.assets" <pathId> "dialog_jp.txt"
```

**Step 3 — Deploy**:
- Copy modified `.assets` files back to the game directory.
- **Always keep a backup** of the original files.

**Bulk replacement pattern**:
- Copy `.assets` to `C:\Temp\uabea_work\`
- `list` to get all pathIds → `exporttext` to match names → `importtext` to import translations
- Deploy back to game directory

**Important notes** (see `../game-unpack/references/uabea_notes.md`):
- `importtext` directly overwrites files — create manual backups first.
- For Unity 5.x: `classdata.tpk` must be present in the UABEA directory.
- Consecutive `importtext` calls may have file handle issues — process one at a time.
- Avoid paths with spaces; use `C:\Temp\`.

**Verification**:
- Launch game, navigate to text-heavy areas, check for:
  - Correct translation display (not original text)
  - No text truncation
  - No garbled characters (encoding mismatch)
  - No missing text (wrong pathId)

### .NET / IL2CPP

**Option A — Direct DLL patching** (only if translation ≤ original length):
- Use dnSpy to locate the string in decompiled source.
- Hex-edit the original bytes or use dnSpy's edit feature.
- Re-sign the assembly if strong-named.

**Option B — BepInEx plugin injection** (recommended):
1. Install BepInEx into the game directory.
2. Create a Harmony patch plugin that intercepts text-getter methods.
3. Bundle `{gamename}_text_translated.csv` as an embedded resource.
4. At runtime: read original text → look up its index in the original `_text.csv` → return the corresponding line from `_translated.csv`.
5. Build and place the plugin DLL in `BepInEx/plugins/`.

### RPG Maker

Replace text in game data files with corresponding lines from `{gamename}_text_translated.csv`. Ensure file encoding matches the original (UTF-8 for MV/MZ). Repackage if the game uses encrypted archives.

### Native Binary

Use REA + Ghidra for all reverse-engineering and patch-location analysis. Read
`../game-unpack/references/rea_ghidra.md` before analysis. The Windows REA
Ghidra provider is read-only: apply changes with a separate reproducible patch
script or injection plugin, verify source hashes and expected bytes, and test
the patched output. Do not treat a virtual address as a file offset.

Prefer patching an understood resource format. Patch bytes in the executable only
when the replacement fits the original allocation and the string is not also an
internal identifier; pad a shorter replacement without shifting later data.

If direct replacement is unsafe or ineffective because of fixed buffers, internal
IDs, startup parsing, or pre-draw text fragmentation, read
`references/native_display_injection.md`. Do not load or apply that reference for
Unity, .NET/IL2CPP, or RPG Maker projects.

## After Repack (Required)

Update `Projects/{GameName}/README.md` with repack results, then commit the progress to the project's git repo (README.md is the shared living doc; see `CODEBUDDY.md` → Git 与 README 协作规范):

```markdown
## 回填
- 工具：UABEAvalonia vX.X / BepInEx vX.X / ...
- 策略：直接替换 / 插件注入
- 修改文件：
  - `resources.assets` — 对话文本（pathId: 123, 456, ...）
- 验证方法：启动游戏，进入主菜单 → 对话 → 设置页面
- 验证结果：通过 / 部分问题（列出）
- 未完成部分：（如有）

## 已知问题
- 字体问题：需要替换为支持中文的字体（路径：...）
- 文本截断：部分 UI 文本过长（列出）
- ...
```

## Troubleshooting

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| Garbled text (�) | Encoding mismatch | Verify original encoding; use same for import |
| Text truncated | Translation longer than original | Use shorter translation or BepInEx injection |
| Game crash on launch | Corrupted asset file | Restore from backup; verify pathId |
| No text appears | Wrong pathId or missing TextAsset | Re-check pathId with `list` command |
| "\u" in text | PowerShell Unicode escaping | Use `cmd /c` or avoid `\u` in paths |
| Order mismatch | Translation lines don't align with original | Cross-check line counts of `_text.csv` and `_translated.csv` |
