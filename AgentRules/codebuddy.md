# CodeBuddy 专属规则

公共工作流（CODEBUDDY.md 与 skills）只写通用要求；本文件记录 CodeBuddy 的具体做法。本目录不参与 `sync_agent_compat.bat` 同步。

## 模型档位

| 档位 | 模型 |
|------|------|
| 便宜档 `cheap` | `deepseek-flash` |
| 标准档 `standard` | `deepseek-flash` |

## 子代理与长耗时任务等待

具体机制尚未整理。按 AGENTS.md「跨 Agent 工具兼容」原则，使用 CodeBuddy 自身的子任务和后台执行机制满足公共工作流的通用要求：

- 每批使用不继承主对话历史的新子代理。
- API 阶段只启动一个 `run_api_round.py`，结束后再处理回执，等待期间不轮询状态或日志。

无法实现时降级为串行，或由主对话直接阻塞运行 runner，并在交付说明中注明。
