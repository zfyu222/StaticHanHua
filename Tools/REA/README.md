# REA + Ghidra

StaticHanHua 的原生逆向工具链。CLI 入口 `rea.ps1`；MCP 注册在工作区 `.mcp.json` 和 `.codex/config.toml`，固定选择 Ghidra。

## 已安装版本

| 组件 | 版本 | 本地位置 |
|---|---|---|
| REA | npm `rea-agents` 6.4.0 | `node_modules/rea-agents/` |
| Node.js | 24.21.0 Windows x64 | `runtime/node-v24.21.0-win-x64/` |
| Ghidra | 12.1.4 | `../Ghidra/ghidra_12.1.4_PUBLIC/` |
| Temurin full JDK | 21.0.12.1+1 Windows x64 | `../Java/jdk-21.0.12.1+1/` |

Node、JDK 和 Ghidra 使用官方 ZIP，下载后核验 SHA-256。运行依赖、分析副本和验证输出不入库；npm manifest/lock 保留用于重建。更新版本时同步启动脚本、两份 MCP 配置，并重新验证实际分析链路。

## 使用

```powershell
& ./Tools/REA/rea.ps1 doctor --provider ghidra --json
& ./Tools/REA/rea.ps1 inspect 'D:/real/game/Game.exe' --provider ghidra --json
```

完整汉化操作规范见 [逆向工作流](../../.agents/skills/game-unpack/references/rea_ghidra.md)。MCP 配置变更需要重启客户端；当前已启动的服务不会因文件编辑自动卸载。

## 重建

从以下官方来源下载对应 ZIP，解压到上表目录，然后使用本地 Node 的 npm 执行 `npm ci --prefix Tools/REA`（将该 Node 目录临时放到 PATH 前端）：

- [Node.js 24.21.0](https://nodejs.org/dist/v24.21.0/)，`node-v24.21.0-win-x64.zip`，SHA-256 `158f7685b44de51f6c0df1d153526cbcd3e1bc739a8dfc607721cef75de9e541`。
- [Ghidra 12.1.4](https://github.com/NationalSecurityAgency/ghidra/releases/tag/Ghidra_12.1.4_build)，`ghidra_12.1.4_PUBLIC_20260921.zip`，SHA-256 `ddac49f903da9d5bac833e5cc79395098b9c33cfd3279be5f31bd00387d2d4db`。
- [Temurin JDK 21.0.12.1+1](https://github.com/adoptium/temurin21-binaries/releases/tag/jdk-21.0.12.1%2B1)，`OpenJDK21U-jdk_x64_windows_hotspot_21.0.12.1_1.zip`，SHA-256 `f9d6e191ab098c0d416e7d588a24420a8621cd2f4720dab2459b8b7b2d2d8b4e`。

## 验证记录

2026-10-13：Ghidra 定向 doctor 全部必需检查通过。用 MSVC 编译本地 x64 原生 PE 测试程序，CLI 成功搜索到两个预期字符串；通过配置中的 stdio MCP 完成 open_binary → search_strings → xrefs → resolve_containing_procedure → procedure_pseudo_code → close_binary，反编译结果正确恢复了两个字符串间的条件选择。输入 SHA-256 分析前后保持一致。两套本地提取/回填技能校验通过，JSON/TOML 配置解析通过。验证输出留在忽略的 verification/；此结果不代表所有游戏或架构均已验证。