# 工具 fork 与构建清单

主仓库通过 Git submodule 固定这些工具的 fork。每个 fork 保留上游 remote（建议命名为 `upstream`），`origin` 指向 `zfyu222` 名下的 fork。

## Fork 迁移目标

| 本地目录 | 上游仓库 | 目标 fork | 本地自定义源码（待提交） |
|---|---|---|---|
| `AINiee` | `AiNiee` | 已存在：`zfyu222/AiNiee` | `LLMClientFactory.py`、`ExclusionListPage.py`、`TableHelper.py` |
| `AssetRipper-CLI` | `MeikoMei16/AssetRipper-CLI` | `zfyu222/AssetRipper-CLI` | 无 |
| `BepInEx` | `BepInEx/BepInEx` | `zfyu222/BepInEx` | 无 |
| `dnSpy-source` | `dnSpy/dnSpy` | `zfyu222/dnSpy` | `dnSpy.Console/Program.cs` |
| `UABEA` | `nesrak1/UABEA` | `zfyu222/UABEA` | `CommandLineHandler.cs`、`Program.cs`、`AssetContainer.cs`、`AssetWorkspace.cs` |
| `XUnity.AutoTranslator` | `bbepis/XUnity.AutoTranslator` | `zfyu222/XUnity.AutoTranslator` | 无 |
| `REA/source` | `morluto/rea` | `zfyu222/rea` | 无；固定到 `rea-agents-6.4.0` |

`dnSpy-source` 含上游子模块；初始化本工作区时使用 `git submodule update --init --recursive`，以获取这些嵌套依赖。

## 迁移步骤（每个工具）

1. 在 GitHub 创建对应 fork，并保留其开源许可证与上游提交历史。
2. 将本地 `origin` 改为自己的 fork，另加 `upstream` 指向表中的上游仓库。
3. 只暂存源码修改，绝不使用无差别的 `git add -A`；尤其不要提交 `venv/`、`__pycache__/`、`bin/`、`obj/`、`artifacts/`、日志或 ZIP。
4. 提交、推送至 fork，必要时从 `upstream` 拉取更新并解决冲突。
5. 在主仓库用 `git submodule add` 或更新子模块指针，记录该 fork 的已验证 commit。

## 已知本地生成物

- AINiee：`venv/` 和 `extracted_for_ainiee_translated.csv`。
- dnSpy：`Tools/dnSpy-net-win64/` 是预编译运行目录，不是源码 fork。
- UABEA、BepInEx、XUnity.AutoTranslator：仓库内可能有上游已追踪的第三方 DLL；不要为“清理编译产物”而删除上游已追踪文件。
- REA：源码由 `Tools/REA/source/` 子模块管理，origin 为 `zfyu222/rea`，upstream 为 `morluto/rea`。`Tools/REA/` 保存框架集成用的 npm manifest/lock 和启动脚本，匹配版本的发布包装在 `node_modules/`；独立 Node 运行时在 `runtime/`。Ghidra、JDK 分别放在 `Tools/Ghidra/`、`Tools/Java/`，这些本地依赖均不入库。源码固定版本与 npm 版本应同步更新；安装版本和校验值见 [REA 工具说明](Tools/REA/README.md)。

## 验证

在任何 fork 推送前，至少运行受影响工具的构建命令；UABEA 与 dnSpy 的自定义命令行功能还应以一次实际 `list` / `exporttext` 或反编译命令验证。
