# Claude Code 专属规则

公共工作流（CLAUDE.md 与 skills）只写通用要求；本文件记录 Claude Code 的具体做法。本目录不参与 `sync_agent_compat.bat` 同步。

## 模型档位

| 档位 | 模型 |
|------|------|
| 便宜档 `cheap` | `haiku` |
| 标准档 `standard` | `opus` |

别名自动指向该系列最新版本，创建子代理时直接作为 Agent 工具的 `model` 参数传入。

## 子代理

- 用 Agent 工具创建新的子代理（不使用 `fork` 类型），新子代理不继承主对话历史，相当于 Codex 的 `fork_turns=none`。
- 每批一个新子代理，按任务档位传 `model`；验收后不再给它派下一批。
- `claim --worker` / `batch-open --workers` 使用 Agent 工具返回的子代理 ID。
- 子代理在后台运行，完成时 Claude Code 自动通知主对话；等待期间不轮询、不 sleep。

## 长耗时任务等待（API 阶段）

不创建执行子代理，主对话直接用命令工具在后台运行 runner：

1. 用 Bash 工具 `run_in_background: true` 执行一次：`<python> D:/StaticHanHua/Tools/localization/run_api_round.py --project <project> --stage <stage>`；显式恢复时才附主流程已核实的 `--sessions <IDs...>`。路径加引号，不拼接不可信命令。
2. 进程结束时 Claude Code 自动唤醒主对话。等待期间不读取输出文件、不 tail 日志、不运行 status、不 sleep 轮询，也不另开第二个 runner。
3. 唤醒后只读取一次 runner 输出的 JSON 回执及必要的 receipt 文件，按公共工作流处理 next_action、stage_clean 和升级派发。
4. 用户要求停止消耗时，立即终止该后台任务及其子进程，不重试、不恢复。

这样省去执行子代理的 Token 开销；runner 内部的选路、重试、预算及停止条件与其他工具完全相同。
