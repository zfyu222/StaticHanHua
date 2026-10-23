---
name: game-unpack
description: |
  This skill should be used when the user needs to extract/unpack text from game files as Step 1 of the StaticHanHua 3-step localization pipeline. It covers Unity AssetBundle extraction, .NET/IL2CPP DLL decompilation, RPG Maker data extraction, and native binary reverse engineering. Trigger when the user says "解包", "提取文本", "extract text from game", or "unpack game resources". The output must be a single-column CSV: one text per line, saved as {gamename}_text.csv.
---

# Game Unpack (解包) — 游戏文本提取

## Overview

Extract readable text from game files as Step 1 of the 3-step StaticHanHua pipeline. Output is a single-column file: one original text per line.

## Workflow

1. **Read the project README** at `Projects/{GameName}/README.md` for existing game info.
2. **Identify the game engine** — check the game files to determine the engine.
3. **Extract text** following the engine-specific guide below.
4. **Save output** as `Projects/{GameName}/{gamename}_text.csv`.
   用户启用图片文字翻译时，另导出可读图片及引擎资源/精灵来源清单，交 `game-image-extract` 在正式翻译前抽取和视觉确认。不要把OCR候选直接拼入常规原文CSV，也不要因未选择图片步骤就默认全量扫描图片。
5. **Update README.md** — write extraction tech stack (tools, commands, key steps, issues encountered) into the 文本提取 / 技术栈 sections.
6. **Commit to git** — in the project directory, stage the CSV + README and commit (README.md is the shared living doc maintained by all three steps; see workspace `CODEBUDDY.md` → Git 与 README 协作规范):
   ```powershell
   cd Projects/{GameName}
   git add {gamename}_text.csv README.md
   git commit -m "提取: <brief>"
   ```

## Output Format

`{gamename}_text.csv` — single column, one original text per line:

```
おはようございます
さようなら
今日はいい天気ですね
ありがとうございます
```

> No header row. No CSV quoting needed (unless the text itself contains commas/newlines).

The file extension is `.csv` but the format is essentially a plain text file with one text per line. This keeps it simple for both machine and manual processing.

## Engine-Specific Extraction

### Unity (AssetBundle / .assets)

**Tool**: UABEAvalonia CLI (`Tools/UABEA/UABEAvalonia/bin/Release/net8.0/UABEAvalonia.exe`) or AssetRipper-CLI.

**Step 1 — Locate text assets**:
- Common locations: `*_Data/resources.assets`, `*_Data/sharedassets*.assets`, `StreamingAssets/*.bundle`
- Use `list` command to enumerate TextAssets (classId=49):
  ```powershell
  .\UABEAvalonia.exe list "path/to/resources.assets" 49
  ```

**Step 2 — Export text**:
- Use `exporttext` for each identified TextAsset:
  ```powershell
  .\UABEAvalonia.exe exporttext "resources.assets" <pathId> "output.txt"
  ```

**Step 3 — Merge to single output**:
- Collect all exported `.txt` files, merge into one `{gamename}_text.csv`.
- Each original text on its own line. Remove duplicates if needed.

**Fallback with AssetRipper-CLI**:
```powershell
.\AssetRipper.Tools.ExportRunner.exe inspect "./Game"
.\AssetRipper.Tools.ExportRunner.exe export "./Game" --output "./output" --mode primary
```
Then search exported assets for text files and merge into `{gamename}_text.csv`.

**Important notes** (see `references/uabea_notes.md`):
- `classdata.tpk` must be in the same directory as `UABEAvalonia.exe`.
- Unity 5.x without TypeTree: `list` command's Name column may be empty; use `exporttext` to match names by content.
- Avoid paths with spaces; use `C:\Temp\` for intermediate work.

### .NET / IL2CPP (Unity, Unreal, etc.)

**Tool**: dnSpy (`Tools/dnSpy-net-win64/dnSpy.exe` GUI or `bin/dnSpy.Console.exe` CLI).

**Step 1 — Decompile**:
```powershell
cmd /c "Tools\dnSpy-net-win64\bin\dnSpy.Console.exe -r ./Game/*_Data/Managed -o ./decompiled"
```
> In PowerShell, use `cmd /c` to avoid encoding issues.

**Step 2 — Extract strings**:
- Open decompiled C# source and search for hardcoded strings.
- Common patterns: `"text"`, `GetText("key")`, `Localization.Get("id")`.
- For IL2CPP: strings may be in `global-metadata.dat` — use Il2CppDumper first. For native code in `GameAssembly.dll` or equivalent, use REA + Ghidra and read `references/rea_ghidra.md`; dnSpy does not decompile IL2CPP native machine code.

**Step 3 — Save**:
- Compile all strings into `{gamename}_text.csv`, one per line.

### RPG Maker

**Tool**: StevExtraction (bundled with AiNiee at `Tools/AINiee/StevExtraction/`).

Run StevExtraction against the game directory. Merge the output into `{gamename}_text.csv`.

### Native Binary (C/C++ executables)

**Tool**: REA + Ghidra (provider fixed to `ghidra`; project-local CLI: `Tools/REA/rea.ps1`).

Read `references/rea_ghidra.md` before native analysis. Use REA to search strings, inspect references and decompile text-loading functions through Ghidra. Export verified display text into `{gamename}_text.csv`; retain source hashes and address/file-offset mappings separately for repacking. All reverse-engineering work uses REA + Ghidra; do not select another native provider.

If the native game has fragmented sources, generated display strings, or incomplete
static coverage, read `references/native_text_extraction.md`. Do not load or apply
that reference for Unity, .NET/IL2CPP, or RPG Maker projects.

## After Extraction (Required)

Update `Projects/{GameName}/README.md` with extraction details, then commit the progress to the project's git repo (README.md is the shared living doc maintained by all three steps; see `CODEBUDDY.md` → Git 与 README 协作规范):

```markdown
## 文本提取
- 文本位置：`resources.assets` / `Assembly-CSharp.dll` / ...
- 工具：UABEAvalonia vX.X / dnSpy vX.X / ...
- 命令：`.\UABEAvalonia.exe exporttext ...`
- 提取文本数量：XXX 条
- 注意事项：编码确认、路径问题、未提取到的文本
```

## References

- `references/uabea_notes.md` — UABEA CLI known issues and fixes
- `references/assetripper_notes.md` — AssetRipper-CLI usage details
- `references/native_text_extraction.md` — native-binary fallback only; ignore for other engines
