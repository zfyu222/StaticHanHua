# REA + Ghidra 逆向工作流

需要原生代码逆向时读取本页：原生 EXE/DLL、IL2CPP 的 GameAssembly.dll、文本加载/编码/字体/排版函数及回填定位。统一使用 REA + Ghidra，显式选择 `ghidra`；工具失败时排查依赖或目标支持，不自动切换其他后端。

## 本机入口与依赖

- CLI：`& D:/StaticHanHua/Tools/REA/rea.ps1 <command> <arguments>`。
- MCP：`.mcp.json` 和 `.codex/config.toml` 中的 `rea`，`REA_ANALYSIS_PROVIDER=ghidra`。修改配置后重启客户端。
- 已固定版本和安装方法见 `Tools/REA/README.md`。先运行 `doctor --provider ghidra --json`，检查 `healthy` 和 `scope_checks`；其他未安装后端或全局客户端注册的 informational checks 不代表本项目 Ghidra 不可用。
- Windows 后端有平台/格式限制。以已安装版本的 `providers --json`、目标会话可用操作和实际错误为准，不能把主分支功能当作已发布能力。

## 目标路径与证据

Windows 原生分析要求本地 NTFS 普通路径，不接受软连接/junction/reparse point、UNC 等路径。不要把 `Projects/{GameName}/Game/...` 直接交给 REA。使用游戏真实路径；不符合要求时只复制需要的 EXE/DLL 到 `Projects/{GameName}/Analysis/input/`，保留原始路径、复制前后 SHA-256 和文件大小。分析副本不纳入 Git 或发布补丁。

输出统一放 `Analysis/`。README 记录真实输入的哈希、工具版本、命令、映像基址、已验证结论和待确认事项。来源映射需记录 CSV 行号、文件/资源、字符串地址、实际文件偏移、编码、分配长度、引用函数以及显示文本/内部键的分类。虚拟地址、RVA 和文件偏移必须分别记录；依据 PE 节映射转换，不能直接混用。

## 提取与函数追踪

示例中的文件路径、字符串和地址必须替换成实际目标；CLI 每次调用是独立分析进程，大目标优先用同一个 MCP 会话完成连续查询。

```powershell
& D:/StaticHanHua/Tools/REA/rea.ps1 inspect 'D:/real/game/Game.exe' --provider ghidra --json
& D:/StaticHanHua/Tools/REA/rea.ps1 search 'D:/real/game/Game.exe' 'New Game' --provider ghidra --json
& D:/StaticHanHua/Tools/REA/rea.ps1 xrefs 'D:/real/game/Game.exe' 0x140001000 --provider ghidra --json
& D:/StaticHanHua/Tools/REA/rea.ps1 decompile 'D:/real/game/Game.exe' 0x140001000 --provider ghidra --json
```

MCP 先检查工具列表，再用 `open_binary` 指定 `path` 和 `provider_id: "ghidra"`；当前 6.4.0 的分析工具包括 `search_strings`、`xrefs`、`resolve_containing_procedure` 和 `procedure_pseudo_code`。从字符串地址查引用后，先将指令地址解析为所属函数入口，再以入口地址反编译；不能假设原始函数名仍被保留。按当前工具 schema 传参，最后 `close_binary` 释放会话。不要猜测工具参数。大型文件首次导入较慢，检查分析状态和错误后处理超时，不盲目并发启动多个 Ghidra 导入。

从已确认的显示文本建立单一 `{gamename}_text.csv`，保留格式化符号、转义和有效空白。字符串列表不等于全部可翻译文本：沿引用寻找加载器、外部资源、模板拼接和字形处理路径。存在片段文本或静态覆盖不足时继续读取 `native_text_extraction.md`。IL2CPP 先结合元数据工具定位，再用本工具链核实原生函数。

## 回填与验证

Windows REA Ghidra 后端的已验证边界是只读分析，不将其当作二进制写入器或运行时调试器。根据证据选择资源回填、可复现补丁脚本或注入插件；动态观察使用另行明确选择的调试/日志工具，不能宣称 REA 已提供未验证的能力。

补丁脚本修改副本前校验支持版本的 SHA-256、预期原字节、文件偏移和长度；长度不足时设计资源重建或显示层注入，不盲目覆盖相邻数据。生成后重新检查目标，并完成启动、菜单、对话、存档和中文字体/换行的游戏内验证。逆向成功不等于汉化验证通过。更新项目 README 并在该项目独立仓库提交阶段成果。

官方参考：[REA CLI](https://github.com/morluto/rea/blob/main/docs/cli.md)、[Windows Ghidra 支持](https://github.com/morluto/rea/blob/main/docs/windows-ghidra-p0.md)、[Ghidra 发布](https://github.com/NationalSecurityAgency/ghidra/releases)。
