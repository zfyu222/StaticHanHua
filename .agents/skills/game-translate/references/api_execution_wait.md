# API 阶段自动多轮与等待

API 路线一个阶段只运行一次 `run_api_round.py`，由它在同一进程内推进本阶段的常规多轮，主代理派发后等待其结束再处理结果，不在各常规轮之间唤醒主 AI。程序复用已有整轮检查与失败项重派入口及内存缓存。质量规则、标准档验收及整轮修复边界不变，范围和停止条件见 [自动循环边界](api_cycles.md)。

后台运行与唤醒的具体做法（子代理还是后台命令、用哪档模型、如何等待）见当前 Agent 工具的规则文件 `AgentRules/<工具>.md`。

## 主代理

1. 核对项目、当前阶段和待执行轮次，没有需要先恢复的旧请求。只有用户已选择并授权 API 执行时才派发实际运行。
   核对选路授权：已有优先级时默认命令不带 `--api`，由程序按配置选路。主代理不能把自行选择接口的决定交给执行者。仅用户明确点名接口才允许 `--api <名称>`；恢复任务也不例外。超时、格式失败、推理耗尽不构成切官方或扩大费用边界的授权。
2. 按工具规则在后台执行一次：`<python> D:/StaticHanHua/Tools/localization/run_api_round.py --project <project> --stage <stage>`。以参数数组或正确引号传递路径，不拼接不可信命令；显式恢复时才附主流程已核实的 `--sessions <IDs...>`。若交给执行子代理，只交付绝对项目路径、阶段、Python 路径和必要的恢复 session ID，不复制主对话历史、术语表、批次 JSON、API 密钥或日志。
3. 等待期间不轮询状态、tail 日志、定时读取 receipt 或反复询问执行者；不启动第二个 runner。用户询问进度时简短回应；明确要求状态才按需查询一次，不取消或重启原任务。
4. 只在本阶段普通循环完成、明确失败、需要主流程决策或用户新指令时处理结果。读取一次紧凑回执及必要 receipt，检查 next_action、stage_clean 及升级派发信息；translate 完成后先由主 AI 检查或提交，再单独调用 draft_check；draft_check/review/final_review 内的扫描和后续常规轮已由程序调用，不为每轮重启 runner。试译决定、术语冻结、阶段切换及标准档任务仍由主流程负责。

## 执行者边界

无论由子代理还是后台命令执行：按用户原优先级运行，不自行添加 `--api`；错误不构成切换官方、修改优先级或增加请求/Token/尝试预算的授权。不查看密钥文件，不翻译或修改结果，不跑质量裁决，不切换阶段或放宽门槛；错误、锁冲突、不明在途请求及标准档升级只报告，不自行重放付费请求。只返回 `status`、`stage`、`receipt_file` 及简短计数/阻塞原因，不返回原文、整批译文、提示词或完整日志。收到用户停止消耗的指令时立即终止 runner 及其子进程，不重试、不恢复。

试译本轮翻译结束后调用 pilot-report，由主 AI 阅读原译文、机械诊断和任务状态，整体判断流程、风格及共性错误，用 pilot-decide 记录 accept/retry/stop。按需调整方案后明确 pilot-retry；不自动派独立审校或逐句修复。正式阶段普通质量修复按原规则整轮处理，默认累计 6 轮后升级标准档。

不得承诺完全零唤醒或零 Token；超时、新消息和平台行为都可能恢复主代理。所用工具不支持后台完成通知时明确说明能力缺口，退回有界等待，不假称具备事件回调。

## 程序入口与边界

runner 的程序日志默认包含性能计时、每 10 个工作单位或事件间隔超过 30 秒的进度，以及末尾总用时与 Token 总结；这是本地程序事件日志，不要求主代理或执行者轮询、发送心跳或定期读取。回执新增 profile_log、profile_summary、elapsed_seconds 和 performance；JSONL 位于 logs/api_runs，完整性能汇总位于 reports/api_runs。Token 区分本轮与项目累计，未知用量不得当零消耗；失败回复和重试的已报告用量也计入，缓存/推理子项不重复累加。

`run_api_round.py` 使用 Popen + wait 阻塞等待 translation_workflow.py run-api，一次调用执行一个阶段的普通多轮，不自动串联语义阶段。translate 只收齐草稿后交回主 AI；程序审校单独使用 --stage draft_check。draft_check/review/final_review 默认自动检查与常规返修；`--single-round` 仅作调试。stdout/stderr 写入 `TranslateFiles/logs/api_runs/`，最终 receipt 写入 `TranslateFiles/reports/api_runs/`，对工具只输出一份 JSON 回执。Python run_api 的 continuous=False 对应调试单轮。

`completed` 仅表示本轮 API 执行正常返回、没有暂停/待处理异常/在途交接，不代表全文质量通过。传输重试已经成功时 failed 计数可能非零；不能因此重译通过项。`needs_attention`、`failed`、`interrupted` 交主流程按现有恢复协议处理，不自动再次运行。

`TranslateFiles/state/api_round.lock` 防止重复启动包装器；普通启动不回收异常遗留锁。显式 `--resume` 时先核对旧 owner_pid 和子进程 pid 均已结束，才回收锁并接续：已有完整回复先收集，成功提交任务跳过，其余未完成任务按原尝试预算重试。不能仅因锁时间久就重新请求。该锁只保护通过本入口启动的轮次；所有 API 自动执行统一走本入口，不另行直接启动重叠的 run-api。

文件 receipt 本身不会唤醒主代理；唤醒来自所用工具的完成通知。

单个格式重试耗尽后执行槽继续本轮其他独立任务，轮末一次返回所有异常；不可重试服务故障仍暂停。主对话等待规则不变，不为单批中间失败唤醒主对话。
