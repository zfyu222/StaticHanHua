# StaticHanHua - 游戏汉化项目

## 项目概述

本项目是游戏汉化工作区，用于对各类游戏进行静态汉化（反编译、文本提取、翻译、回填）。

## 目录结构

```
StaticHanHua/
├── Projects/          # 各游戏汉化项目
│   └── {GameName}/
│       ├── README.md   # 【必读】项目档案（三步共同维护的活文档）
│       ├── Game/      # 游戏文件软连接（指向实际游戏目录，不纳入 git）
│       ├── .git/      # 该游戏的独立 git 仓库（由 start-translate 初始化，仅管汉化工程）
│       ├── {gamename}_text.csv            # 第1步：提取的待翻译文本（1列，1个文本1行）
│       ├── {gamename}_text_translated.csv # 第2步：翻译完成后的文本（对应原文逐行翻译）
│       └── TranslateFiles/               # 翻译配置、术语、任务、报告、缓存等中间产物
│
├── Tools/             # 工具集
│   ├── AINiee/        # AI 翻译工具（主翻译引擎）
│   ├── dnSpy-net-win64/  # .NET 反编译工具
│   ├── AssetRipper_win_x64/  # Unity 资源提取工具（旧版 GUI）
│   ├── AssetRipper-CLI/  # Unity 资源提取工具（命令行版，推荐）
│   ├── UABEA/           # UABEAvalonia（跨平台 Unity Asset Bundle 编辑器，支持 CLI）
│   └── ...            # 新工具放此处
│
└── .mcp.json          # MCP 服务器配置（含 REA + Ghidra MCP）
```

## 汉化流程（四步走）

### 第 1 步：提取文本（解包）

> 使用 `game-unpack` Skill 从游戏中提取原始文本。

| 游戏引擎 | 提取工具 | 输入 | 输出 |
|----------|----------|------|------|
| .NET / IL2CPP | 托管 DLL 用 `dnSpy`；IL2CPP 元数据提取 + REA/Ghidra 原生逆向 | DLL / `global-metadata.dat` | `{gamename}_text.csv` |
| Unity | `AssetRipper-CLI` / `UABEAvalonia` 导出资源 | `Game/` 游戏目录 | `{gamename}_text.csv` |
| RPG Maker | `StevExtraction`（AiNiee 子工具） | `Game/` 游戏目录 | `{gamename}_text.csv` |
| 原生二进制 | REA + Ghidra | 可执行文件 | `{gamename}_text.csv` |

**输出格式**：`{gamename}_text.csv`

- **1 列，1 个文本 1 行**（纯文本逐行排列）
- 示例：
  ```
  こんにちは
  さようなら
  おはようございます
  ```

**完成后必须**：将提取所用的技术栈、工具版本、关键步骤写入 `README.md`，并在项目目录提交 git（`git add {gamename}_text.csv README.md && git commit`，详见下方「Git 与 README 协作规范」）。

### 第 2 步：翻译

> 使用 `game-translate` Skill 完成翻译阶段。保留 AINiee 工作流，也可选择自定义工作流；自定义可用 Codex 子代理或 API 程序执行。内部规则与工具见该技能，不在基础文档展开。

首次执行让用户选择工作流，已明确选择直接沿用。源文和正式译文在项目根目录；所有翻译中间产物统一放 `TranslateFiles/`，包括 `TranslateDoc.md`、配置、术语审校、任务、结果、报告、缓存与备份。

| 项目 | 说明 |
|------|------|
| **输入** | `{gamename}_text.csv`（1 列，逐行原文） |
| **输出** | `{gamename}_text_translated.csv`（逐行译文，与原文一一对应） |
| **方式** | AINiee 工作流 / 自定义工作流（Codex 子代理或 API） / 手动翻译 |
| **AI 接口** | DeepSeek（推荐）、OpenAI、Anthropic、Google 等 |

**翻译流程**：
1. 将 `{gamename}_text.csv` 作为翻译源文件。
2. 若使用 AI 辅助翻译（AiNiee）：
   - 启动 AINiee：`cd Tools\AINiee && .\venv\Scripts\python.exe AiNiee.py`
   - 在「接口管理」中添加并激活 API（如 DeepSeek）
   - 新项目导入 `TranslateFiles/source/{gamename}_text.csv` 源文快照，设置源语言/目标语言；既有项目沿用已保存的缓存与进度
   - 先提取项目术语（全文分析），完成下面的固定术语审校步骤并应用审校版
   - 点击「开始翻译」
3. 翻译完成后将译文保存为 `{gamename}_text_translated.csv`，与原文逐行一一对应。

**共同要求**：正文前先完成全文术语整理与语义审校，再冻结使用；程序检查通过不等于语义审校完成。两种工作流都需保护占位符并完成译后校验。具体内部阶段、调度、缓存与修复规则由 `.agents/skills/game-translate/` 维护。缓存、备份和凭据不入库。

**翻译过程中**：如果有需要注意的信息（字体选择、术语表、特殊语境说明、文本分组等），补充到 `README.md` 中，供打包阶段参考。

**完成后必须**：将翻译方式、占位符/禁翻表、校验结果写入 `README.md` 的「翻译」节，并提交 git（`git add {gamename}_text_translated.csv README.md TranslateFiles/TranslateDoc.md && git commit`）。

### 第 3 步：打包回填

> 使用 `game-repack` Skill 将翻译后的文本重新打包回游戏中。

| 游戏引擎 | 回填方式 | 输入 | 输出 |
|----------|----------|------|------|
| .NET / IL2CPP | 替换原 DLL 中的字符串 / 使用 BepInEx 插件注入 | `{gamename}_text_translated.csv` | 汉化版 DLL 或翻译插件 |
| Unity | 替换 .assets / AssetBundle 中的文本 | `{gamename}_text_translated.csv` | 汉化版资源文件 |
| RPG Maker | 替换游戏数据文件中的文本 | `{gamename}_text_translated.csv` | 汉化版数据文件 |

**回填策略**：
- **直接替换**：修改原游戏文件中的字符串（需注意编码和长度限制）
- **插件注入**：使用 BepInEx 等框架动态注入翻译，不修改原文件，兼容性好

**完成后必须**：将回填所用的技术栈、工具版本、验证结果、已知问题写入 `README.md`，并提交 git（`git add README.md && git commit`；汉化产物视体积决定是否入库，`Game/` 永不提交）。

### 第 4 步：对外发布补丁

> 使用 `game-release-patch` Skill 将已验证的汉化成果制作成可分发的 ZIP 补丁。

- **输出目录**：`Projects/{GameName}/Dispatch/`
- **ZIP 命名与版本**：固定为 `{游戏名}_子非鱼汉化{版本号}.zip`。首次发布为 `1.0`；以后每次对外发布取现有最高版本加 `0.1`。补丁标题同步使用版本号，默认 `《{游戏名}》子非鱼AI汉化补丁 v{版本号}`。
- **补丁结构**：游戏根目录相对路径的补丁载荷 + 根目录 `补丁说明.txt` + `PATCH_MANIFEST.json` + `SHA256SUMS.txt`
- **压缩前载荷**：将本次发布的文件按游戏根目录相对路径复制到 `Dispatch/Files/`，确认无误后只从该目录生成 ZIP。
- **补丁说明**：标题必须包含「子非鱼AI汉化」，并写明介绍、覆盖范围、支持的游戏版本和平台、汉化日期、AI 汉化提示、安装/卸载方法、验证结果与已知问题。
- **后续更新提醒**：发布说明必须提醒玩家「补丁后续可能会更新，建议关注或订阅本项目的 GitHub 仓库 / Releases，或收藏、订阅网盘发布渠道，及时获取新版补丁」。已有公开链接时附上对应链接，不编造地址。
- **更新日志**：首次发布在说明书末尾记录 `v1.0：首次发布`；每次升级必须把本版更新内容追加到说明书末尾，保留历次记录。
- **安全边界**：不得打包 `Game/`、游戏本体、原始可分发资源、密钥或开发日志；仅包含玩家安装补丁所需的文件。
- **最终上传**：本地校验通过后，通过百度网盘 MCP 先调用 `ensure_directory("/Hanhua/{GameName}")`，再调用 `delete_existing_patch_versions` 删除该目录中同游戏的旧版 `{GameName}_子非鱼汉化*.zip`，最后上传新 ZIP 到 `/Hanhua/{GameName}/`。不得删除目录中的其他文件。
- **完成后必须**：在项目 `README.md` 的「对外发布」节记录补丁名称、支持版本、载荷、验证结果与已知问题，并提交文档（大体积 ZIP 和二进制产物默认不入库）。

## 工具分类

### 反编译工具
- **dnSpy** — `Tools/dnSpy-net-win64/` — .NET 程序集反编译与调试
  - GUI 入口：`dnSpy.exe`
  - 命令行入口：`bin\dnSpy.Console.exe`（已修复中文 Windows 编码问题）
    - 源码位置：`Tools/dnSpy-source/`（修复了 `dnSpy.Console/Program.cs` 中 `SetConsoleOutputEncoding` 在中文系统崩溃的问题）
    - 编译命令：`dotnet build dnSpy\dnSpy.Console\dnSpy.Console.csproj -f net5.0-windows`
    - 需要 .NET 5.0 Desktop Runtime（`winget install Microsoft.DotNet.DesktopRuntime.5`）
  - 常用 CLI 参数：
    - `-o <dir>` — 指定输出目录
    - `-r` — 递归处理整个目录下的所有程序集
    - `-t <type>` — 精准提取指定类型
    - `--md <token>` — 按元数据令牌定位（如 `0x02000003`）
    - `--threads <n>` — 多线程并发数
    - `--sln-name <name>` — 生成的 .sln 文件名
    - `--vs <ver>` — 指定 Visual Studio 版本（如 `2022`）
    - `--sdk-project` — 生成 SDK 风格的项目文件
    - `--no-resources` — 仅反编译代码，不提取资源
    - `--no-baml` — 跳过 BAML 文件反编译
    - `--no-resx` — 保留原始资源格式
    - `--asm-path <path>` — 指定自定义程序集搜索目录
    - `--user-gac` — 使用用户 GAC
    - `--no-color` — 禁用彩色输出
  - 示例：
    ```bash
    # 反编译单个 DLL
    dnSpy.Console.exe Assembly-CSharp.dll -o ./decompiled
    # 递归反编译整个目录
    dnSpy.Console.exe -r ./Game_Data/Managed -o ./decompiled
    # 导出为 SDK 风格项目
    dnSpy.Console.exe Assembly-CSharp.dll -o ./project --sln-name Game --sdk-project --vs 2022
    ```
  - 注意：PowerShell 下可能因编码问题报错，可通过 `cmd` 运行
- **REA + Ghidra** — 通过 `.mcp.json` / `.codex/config.toml` 配置 REA MCP，固定使用 Ghidra 后端。入口 `Tools/REA/rea.ps1`；原生 EXE/DLL、IL2CPP 原生代码及其他需要逆向定位的环节均使用此工具链。操作规范见 `.agents/skills/game-unpack/references/rea_ghidra.md`。

### 提取工具
- **AssetRipper (GUI)** — `Tools/AssetRipper_win_x64/` — Unity 游戏资源与脚本提取（旧版 GUI）
- **AssetRipper-CLI** — `Tools/AssetRipper-CLI/` — Unity 游戏资源命令行提取工具（推荐）
  - 入口：`artifacts/bin/AssetRipper.Tools.ExportRunner/Release/net10.0/AssetRipper.Tools.ExportRunner.exe`
  - 基于 `MeikoMei16/AssetRipper-CLI`，从源码编译
  - 需要 .NET 10 SDK（`winget install Microsoft.DotNet.SDK.10`）
  - 核心命令：
    - `inspect <路径>` — 扫描游戏目录，了解资源内容
    - `export <路径> --output <输出> --profile <配置>` — 按类别提取资源
    - `analyze <路径> --report out.json` — 分析并导出 JSON 报告
    - `report <manifest.json>` — 查看导出清单
  - 导出配置（`--profile`）：
    | 配置 | 说明 |
    |------|------|
    | `cg` | 静态 CG 图（事件插图、画廊） |
    | `audio` | 音频（BGM、语音、音效） |
    | `characters` | 角色立绘、头像 |
    | `backgrounds` | 场景/背景美术 |
    | `sprites` | 精灵图 |
    | `ui` | UI 界面元素 |
    | `player-art` | 玩家美术素材 |
    | `narrative` | 叙事/剧情文本素材 |
    | `full-raw` | 全量原始提取（无过滤） |
    | `full-project` | 完整 Unity 工程导出 |
  - 示例：
    ```bash
    # 设别名（PowerShell）
    Set-Alias arc "Tools/AssetRipper-CLI/artifacts/bin/AssetRipper.Tools.ExportRunner/Release/net10.0/AssetRipper.Tools.ExportRunner.exe"
    # 扫描游戏
    arc inspect ./Game
    # 提取 CG
    arc export ./Game --output ./output/cg --profile cg
    # 提取音频
    arc export ./Game --output ./output/audio --profile audio
    # 全量提取
    arc export ./Game --output ./output/all --mode primary
    ```

- **UABEAvalonia** — `Tools/UABEA/` — 跨平台 Unity Asset Bundle 编辑器（新一代 UABE，支持 CLI）
  - 仓库：`https://github.com/nesrak1/UABEA`
  - 入口：`Tools/UABEA/UABEAvalonia/bin/Release/net8.0/UABEAvalonia.exe`
  - 构建：`dotnet build UABEAvalonia\UABEAvalonia.csproj -c Release`（需 .NET 8.0 SDK）
  - 用途：AssetBundle / .assets 文件资源提取、编辑和回填，支持 GUI 和命令行两种模式
  - 适用于第 1 步（文本提取）和第 3 步（资源回填）

  **CLI 命令参考**（`UABEAvalonia.exe <command> [args]`）：

  | 命令 | 说明 | 用法 |
  |------|------|------|
  | `batchexportbundle` | 批量导出 Bundle 资源 | `<目录> [-keepnames] [-kd] [-fd] [-md]` |
  | `batchimportbundle` | 批量导入修改后的资源 | `<目录> [-keepnames] [-kd] [-fd] [-md]` |
  | `applyemip` | 应用 .emip 安装包 | `<emip文件> <目录>` |
  | `list` | 列出所有资源 | `<assetsFile> [classId]` |
  | `export` | 导出单个资源（原始二进制） | `<assetsFile> <pathId> <outputFile>` |
  | `import` | 导入单个资源（原始二进制） | `<assetsFile> <pathId> <inputFile>` |
  | `exporttext` | 导出 TextAsset 文本内容 | `<assetsFile> <pathId> <outputFile>` |
  | `importtext` | 导入文本到 TextAsset | `<assetsFile> <pathId> <inputFile>` |
  | `exportdump` | 导出为 UABE 文本格式 | `<assetsFile> <pathId> <outputFile>` |
  | `importdump` | 从 UABE 文本格式导入 | `<assetsFile> <pathId> <inputFile>` |
  | `exportjson` | 导出为 JSON 格式 | `<assetsFile> <pathId> <outputFile>` |
  | `importjson` | 从 JSON 格式导入 | `<assetsFile> <pathId> <inputFile>` |

  **示例**：
  ```powershell
  # 列出 .assets 文件中所有 TextAsset（classId=49）
  .\UABEAvalonia.exe list "resources.assets" 49

  # 导出 TextAsset 的文本内容
  .\UABEAvalonia.exe exporttext "resources.assets" 123456 "dialog.txt"

  # 翻译后回填
  .\UABEAvalonia.exe importtext "resources.assets" 123456 "dialog_translated.txt"

  # 导出为 JSON 格式（支持更复杂的数据结构）
  .\UABEAvalonia.exe exportjson "resources.assets" 123456 "asset.json"
  ```

  **批量替换 TextAsset 脚本**：`batch_replace_uabea.py`
  - 用于第 3 步回填，通过 CLI 逐个替换 .assets 中的 TextAsset 文本
  - 工作流程：复制 .assets 到 `C:\Temp\uabea_work\` → `list` 获取所有 pathId → `exporttext` 匹配名称 → `importtext` 导入中文 → 部署回游戏目录
  - 配置：修改脚本顶部的 `REPLACEMENTS` 字典（`TextAsset名称: 中文文件路径`）
  - 注意：importtext 直接覆盖文件（不创建 .bak），脚本自行做备份

  **⚠ 已修复的 CLI 兼容性问题**（2026-07，基于 Expeditions Viking 项目）：

  | 问题 | 症状 | 修复位置 | 修复方式 |
  |------|------|----------|----------|
  | 控制台崩溃 | 所有 CLI 命令无输出、无错误直接退出 | `Program.cs` 第 30 行 | `Console.GetCursorPosition()` 加 try-catch（IOException），非交互式控制台下跳过 |
  | 缺少类型数据库 | `exporttext`/`importtext` 报 NullReferenceException，GUI 正常但 CLI 崩溃 | `CommandLineHandler.cs` 新增 `LoadClassPackage()` 方法 | 在 `LoadAssetsFile` 后调用 `am.LoadClassPackage("classdata.tpk")` + `am.LoadClassDatabaseFromPackage(unityVersion)` |
  | 文件句柄未释放 | `importtext` 连续调用时报 "file being used by another process" | `CommandLineHandler.cs` importtext 方法 | 在 `File.Delete` 前先调用 `am.UnloadAllAssetsFiles()` |
  | importtext 创建备份 | 每次调用移动原文件为 `.bak0000`，无法连续批量操作 | `CommandLineHandler.cs` importtext 方法 | 移除备份逻辑，改为 `File.Delete` + `File.Move` 直接覆盖 |

  **⚠ 未修复但需注意**：
  - `exportdump`/`importdump`/`exportjson`/`importjson` 命令**未加** `LoadClassPackage`，处理无 TypeTree 的 Unity 5.x 游戏时可能崩溃
  - `import`/`importdump`/`importjson` 命令**未加** 文件句柄释放修复，连续调用可能报文件占用
  - `list` 命令的 Name 列对 Unity 5.x 无 TypeTree 文件为空（`GetAssetName` 内部 try-catch 吞了异常），需通过 `exporttext` 逐个导出匹配名称
  - `classdata.tpk` 必须与 `UABEAvalonia.exe` 在同一目录（`net8.0/` 下），重新编译后需确认文件存在
  - 路径含空格时，`cmd /c` 调用需用双引号包裹，且 `\u` 会被误解析为 Unicode 转义，建议用无空格路径（如 `C:\Temp\`）

  **调试经验**：遇到 CLI 与 GUI 行为不一致时，第一步应对比 GUI 入口（`MainWindow.axaml.cs`）与 CLI 入口（`CommandLineHandler.cs`）的初始化代码差异，而非在下游函数中反复调试。

### 翻译工具
- **AINiee** — `Tools/AINiee/` — 主翻译工具，基于 PyQt5 的桌面应用，支持多种 AI 接口（OpenAI、Anthropic、DeepSeek 等），支持游戏文本、字幕、文档等多种格式
  - **启动命令**：`.\venv\Scripts\python.exe AiNiee.py`（需 Python 3.12 虚拟环境）
  - 依赖：`Tools/AINiee/requirements.txt`
  - 虚拟环境：`Tools/AINiee/venv/`（Python 3.12，已安装所有依赖包括 babeldoc）
  - 包含 `StevExtraction/` 子工具用于 RPG Maker 游戏文本提取

## Git 与 README 协作规范

每个游戏项目（`Projects/{GameName}/`）都拥有**独立的 git 仓库**，由 `start-translate` skill 在初始化工作区时创建。该仓库专门管理本游戏的汉化工程，**不管理游戏目录**（`Game/` 软连接已被 `.gitignore` 排除）。

### 提交时机

汉化流程每取得阶段性进展，**必须**提交一次 git：

| 阶段 | 触发 skill | 提交内容 | 提交信息示例 |
|------|-----------|----------|-------------|
| 初始化 | `start-translate` | `README.md` + `.gitignore`（脚本自动提交） | `初始化汉化项目工作区 (start-translate)` |
| 提取 | `game-unpack` | `{gamename}_text.csv` + `README.md`（文本提取节） | `提取: 从 resources.assets 提取 1234 条文本` |
| 翻译 | `game-translate` | `{gamename}_text_translated.csv` + `README.md`（翻译节）+ `TranslateFiles/` 内适合入库的配置、术语审校及交付报告 | `翻译: 完成 AINiee 翻译并校验通过` |
| 回填 | `game-repack` | `README.md`（回填/已知问题节）+ 汉化产物（视体积） | `回填: 替换 12 个 TextAsset，游戏内验证通过` |
| 发布 | `game-release-patch` | `README.md`（对外发布节）+ 小型说明/清单 | `发布: 生成 v1.0 对外汉化补丁` |

### 提交规范

所有 git 操作在项目目录 `Projects/{GameName}/` 下执行：

```powershell
cd Projects/{GameName}
git add README.md {相关产物}
git commit -m "<阶段>: <简述>"
```

- 提交信息前缀用阶段名：`提取` / `翻译` / `回填` / `修复` / `文档`
- 大体积二进制产物（如修改后的 `.assets`、DLL）谨慎入库；必要时用 Git LFS 或仅记录改动说明
- `Game/` 目录**永远不提交**（已被 `.gitignore` 的 `/Game/` 规则排除）
- 若用户未配置全局 git 身份，`start-translate` 已设置本地默认身份；后续提交沿用即可

### README 共同维护

`README.md` 是贯穿三步的**活文档**，由各 skill 在各自环节写入对应章节，不重复、不覆盖他节：

| 维护方 | 负责章节 |
|--------|---------|
| `start-translate` | 生成模板骨架；可预填「游戏信息」「技术栈」可探测项 |
| `game-unpack` | 「文本提取」「技术栈」提取行 |
| `game-translate` | 「翻译」「技术栈」翻译行 |
| `game-repack` | 「回填」「已知问题」「技术栈」回填行、「版本记录」追加 |
| 任意步骤 | 发现的游戏信息（引擎、原语言、字体需求等）回填到「游戏信息」节 |

## 项目 README 规范

> **每个游戏项目必须维护 `README.md`**，作为项目档案，记录后续汉化该游戏所需的一切信息。

`README.md` 应包含以下内容（按需取舍）：

### 必填项
- **游戏基本信息**：游戏名称（原名 + 中文名）、开发商、发行平台（Steam/GOG/Epic 等）、游戏引擎及版本、游戏语言（原语言/支持的语言）
- **汉化目标**：本次汉化的范围（全文本/仅 UI/仅剧情等）、目标语言
- **技术栈总结**：该游戏用到的汉化工具链及版本（如 AssetRipper vX.X、UABEA vX.X、AI 模型等）

### 推荐填写
- **文本提取方案**：文本资源位置（文件路径、资源名称）、提取工具及命令、提取步骤、遇到的坑及解决方法
- **翻译方案**：翻译工具、使用的 AI 接口、词典/术语表位置、分批次翻译策略
- **回填方案**：回填工具及命令、回填步骤、验证方法（如何确认汉化生效）
- **已知问题 & 注意事项**：编码问题、文本长度限制、特殊字符处理、字体替换方案、未完成部分

### 可选
- **资源清单**：CG/音频/立绘等资源提取方式与结果
- **版本记录**：每次汉化操作的日期、操作人、变更内容
- **截图对比**：汉化前后效果对比
- **参考资料**：相关工具文档链接、论坛讨论、同类游戏汉化经验

### 模板

```markdown
# {游戏英文名} 汉化项目

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

## 技术栈
| 环节 | 工具 | 版本 |
|------|------|------|
| 提取 |  |  |
| 翻译 |  |  |
| 回填 |  |  |
| 发布 | `game-release-patch` |  |

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

## 对外发布
- 补丁文件：
- 支持版本/平台：
- 载荷：
- 验证结果：
- 已知问题：

## 版本记录
```

## 项目规范

- 每个游戏在 `Projects/` 下建独立子目录，命名使用英文游戏名
- **每个项目必须创建 `README.md`**，按上方规范填写游戏信息和汉化方案
- 游戏文件通过 `Game/` 软连接引用，不直接复制
- 原文和正式译文放在项目根目录下，与 `Game/` 同级：
  - **第 1 步输出**：`{gamename}_text.csv`（提取的待翻译文本，1 列，1 个文本 1 行）
  - **第 2 步输出**：`{gamename}_text_translated.csv`（翻译完成后的译文，与原文逐行对应）
  - 翻译中间文件统一放在 `TranslateFiles/`；其他阶段中间文件沿用原约定。缓存、备份和凭据不入库
- 新增工具放入 `Tools/` 目录，按功能归类

## 逆向工具链约定

需要逆向分析时统一使用 REA + Ghidra，固定选择 `ghidra` 后端。适用于原生 EXE/DLL、IL2CPP 原生代码、文本/字体/编码/排版函数及回填定位。分析证据与副本放在项目 `Analysis/`，执行前阅读 `.agents/skills/game-unpack/references/rea_ghidra.md`；工具失败时排查依赖和目标支持。
