# StaticHanHua 工具链工作区

这是静态游戏汉化的**工具链源码工作区**。它只管理工作流说明、启动脚本和 `Tools/` 下各工具的 Git 子模块；不包含任何游戏本体、游戏项目或编译产物。

## 仓库边界

- `Projects/`：本机游戏汉化项目与 Steam 游戏目录联接，永久忽略。
- `Tools/`：每个源码工具都是独立仓库，固定到维护者 `zfyu222` 名下的 fork；主仓库通过 Git submodule 记录版本，不复制工具源码。
- `Tools/dnSpy-net-win64/`：本地编译/运行输出，永久不提交；需要时由 `Tools/dnSpy-source/` 构建。
- Python 虚拟环境、`.NET` 的 `bin/`、`obj/`、导出资源、缓存和 ZIP 均为本地生成物，不提交。

## 首次启动

1. 克隆主仓库并初始化所有工具子模块：

   ```powershell
   git clone --recurse-submodules https://github.com/zfyu222/StaticHanHua.git
   cd StaticHanHua
   git submodule update --init --recursive
   ```

2. 安装基础依赖：Git、Python 3.12、.NET SDK 8 与 .NET SDK 10。dnSpy 还需要 .NET 5 Desktop Runtime（及构建它所需的 .NET 5 SDK）。

3. 按需构建工具；只需要使用某项工具时才构建该项。详细命令见下表及 [TOOLS.md](TOOLS.md)。

| 工具 | 依赖 | 本地构建/初始化 |
|---|---|---|
| AINiee | Python 3.12 | `cd Tools/AINiee`; `py -3.12 -m venv venv`; `venv\Scripts\python.exe -m pip install -r requirements.txt` |
| AssetRipper-CLI | .NET SDK 10 | `dotnet build Tools/AssetRipper-CLI/src/AssetRipper.Tools.ExportRunner/AssetRipper.Tools.ExportRunner.csproj -c Release` |
| dnSpy | .NET 5 SDK/Desktop Runtime | `dotnet build Tools/dnSpy-source/dnSpy/dnSpy.Console/dnSpy.Console.csproj -f net5.0-windows` |
| UABEA | .NET SDK 8 | `dotnet build Tools/UABEA/UABEAvalonia/UABEAvalonia.csproj -c Release` |
| REA + Ghidra | Node.js、Ghidra、JDK 21 | 源码子模块 `Tools/REA/source`（`zfyu222/rea`）；运行依赖安装和 CLI/MCP 入口见 [REA 工具说明](Tools/REA/README.md) |
| BepInEx / XUnity.AutoTranslator | Visual Studio 或匹配的 .NET SDK | 仅在需要重新构建插件时打开各自的 `.sln` 并按上游说明构建 |

4. 翻译使用下述共用流程程序；AINiee 的环境和复用模块仍是当前底层依赖。

## 翻译工作流

翻译阶段仅提供 SubAgent（Codex 子代理）和 API（程序请求）两条路线，共用任务准备、术语、审校、恢复与验收规则。源文和正式译文留在游戏汉化项目根目录，翻译中间产物统一放 `TranslateFiles/`。执行规则由 `game-translate` 技能维护，程序入口为 `Tools/localization/translation_workflow.py`，操作参考见 [翻译流程工具说明](Tools/localization/README.md)。

## 逆向工作流

需要原生代码逆向时统一使用 REA + Ghidra：字符串定位、引用追踪、函数反编译、IL2CPP 原生代码分析及回填位置确认。工作区 MCP 注册 `rea`，固定 `ghidra` 后端；CLI 入口为 `Tools/REA/rea.ps1`。依赖安装与验证记录见 [REA 工具说明](Tools/REA/README.md)。

`Game/` 联接路径不能直接交给 Windows REA；使用真实游戏路径或带 SHA-256 记录的分析副本。分析证据放在各游戏项目 `Analysis/`，Windows 后端只读分析，回填通过独立补丁脚本或插件实现并完成游戏内验证。Unity 资源、托管 .NET 和 RPG Maker 继续使用各自专用提取与回填工具。

## 更新工具

先更新对应 fork 的 `main`/`master` 分支并验证，再在此仓库执行：

```powershell
git submodule update --remote --merge
git add .gitmodules Tools
git commit -m "chore: 更新工具子模块"
```

不要在主仓库直接提交工具目录内部的改动；先进入对应工具目录提交并推送到自己的 fork。
