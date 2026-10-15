# Codex 批次交接与局部修复

适用于已经冻结术语的正文阶段。继续现有项目与已完成草稿，不重新 init，不重置配置，不为接入入口重译。术语提取/审校仍使用各自的条目证据协议。

## 两次程序交接

主代理创建实际 Luna 实例，取得真实 ID 后，一次调用程序领取并生成输入包；可同时为多个空闲槽位领取：

```powershell
& $python $flow --project $project batch-open --stage translate --workers <真实实例ID1> <真实实例ID2>
```

返回 session_id、input_file、result_file、条数与简短摘要。直接把 input_file 和 result_file 发送给实例；实例只读一个 input.md，完成后只通知 session_id、完成状态，不输出整批译文。不可把旧协议、指针文件、完整任务 JSON、全表术语和日志再发送一遍。输入含一次协议、一次背景/文体、完整原文/保护文本与标记映射、逐条元数据和实际命中的 src/dst；不复制 evidence_rows 等术语审校字段。

主代理以完成通知驱动处理，不使用 sleep、反复 status、tail 日志或定时读取结果文件。等待使用代理结束通知或事件等待；主代理只在完成、明确失败、需要语义判断时工作，不创建额外 Sol 调度实例。

收到完成通知后，一次程序调用读取、按 ID 保存、提交并汇总状态，可合并处理多份已完成通知：

```powershell
& $python $flow --project $project batch-finish --sessions <session1> <session2>
```

单批完成且已创建下一实际实例时，可加 `--next-worker <新实例ID>`，把本批处理和下一批领取合成一次调用。程序不自行创建模型实例，不假造身份。全文翻译阶段只翻译；全部初稿完成后才进入独立 review 阶段，程序保留原有阶段门槛。

## 全文草稿完成后集中检查

普通 translate 批次只校验任务版本、ID 完整唯一、译文为字符串及锁定约束。空译文、占位符和术语问题不阻塞草稿保存，不当批派回修改；原始候选、保护映射和实际作者持久保留，回执明确 quality=draft_saved。普通词义在首次翻译时即可附有依据的例外。JSON 损坏、未知 ID 或过期租约仍立即报错，防止丢失和串句。

全部草稿保存、在途任务结束后执行一次本地全库扫描；最后一次 batch-open 无任务时返回 drafts_complete 和此 next_action：

```powershell
& $python $flow --project $project draft-scan --prepare-repairs
```

reports/full_draft_scan.json 列出所有失败 ID、完整原文、当前译文和具体诊断。程序保留其余译文，跨原批次、跨场景合并失败 ID 为严格 translate 修复任务（strict_translation=true），沿用正文条数及字符上限；无需人工拼接或重置旧草稿。再次运行不会重复创建已有未完成修复任务。通过 batch-open --stage translate 领取这些修复任务，输入包只包含失败句、当前译文、诊断、必要元数据和相关术语，不重复背景或正常样例。

修复完成后再执行 draft-scan，clean 后 prepare --stage review；程序会阻止带未解决质量问题的正文进入独立审校。程序扫描与最终导出仍严格检查术语、空译文、占位符和结构，语义由独立审校核对。扫描回执可复用，普通审校分批准备不会重复扫描全文；译文或规则变更使回执失效。

## 严格阶段与可靠性修复

试译、集中修复任务及独立审校即时严格校验 ID、占位符、术语、语境例外，以及审校阶段的六维证据。以下 batch-repair 用于这些阶段或正文保存的可靠性问题，不用于普通正文批次的质量返工。翻译实例不运行检查脚本，不反复自审。

结果中可验收的条目持久暂存在 SQLite batch_items；失败条目保留候选文本及具体诊断。完整任务尚未通过时不把该批标记完成。80 条中 2 条失败，78 条不会重新派发。

```powershell
& $python $flow --project $project batch-repair --session <返回repair_required的session> --worker <实际修复实例ID>
# 修复实例只读返回的输入包，写出失败 ID 的结果；结束通知后：
& $python $flow --project $project batch-finish --sessions <修复session>
```

修复包只含失败 ID、完整原文/保护文本、任务原译文、候选译文、诊断、必要元数据及相关术语，不重复背景、通过样例或其他条目。审校修订的 before 对应任务原译文，不是错误候选稿。程序按 ID 合并并执行完整验收；修复后仍失败返回 needs_attention，停止自动重派，交 Sol 判断。每批只自动集中修复一次，仍受原任务/实例/试译预算和租约限制；不能改 ID 或新建任务刷新次数。语法损坏、未知 ID、过期租约等整体错误明确报错，不猜测或丢弃其余内容。

Sol 已判断出具体原因、给出明确处理依据后，可对 needs_attention 的 session 再调用 batch-repair --reason <Sol判断与定向验收要求>。这是有记录的人工主流程判断，不能用于机械循环；仍只携带未解决 ID，保留全部通过项，并消耗原任务剩余尝试次数（默认总共 3 次）及试译预算。无明确原因不允许自动再派。

batch_runs 保存交接，batch_items 保存每条有效结果与实际实例，record_lineage 保存原产出/修复历史；quality_authors 记录每个真实贡献者，独立审校不得由原产出者或修订者承担。最终审校证据按条目保存真实审校实例，不把整批归到最后修复者。旧任务的身份记录继续沿用，不虚构历史作者。

## 普通词义与例外

每条 terms 只包含该原文实际命中的冻结术语。move、Charge、Poison 等词既可能是机制名称，也可能是普通动词/名词；不按大小写或词形自动认定语义。

```json
{"id":"实际ID","translation":"本句译文","exceptions":[{"source":"Charge","reason":"原句在谈服务费用，此处是收费含义，不是冲锋技能。"}]}
```

source 必须指向本条实际命中的 src；reason 要解释原句依据及为何采用普通词义。程序可去空白、接受 src 字段别名和单个对象、归一化唯一 ASCII 大小写匹配；大小写敏感或歧义匹配不自动变更，不补理由、不自动生成例外、不修改冻结表。独立审校复核这些语义声明。

冻结表的逐条译名是权威来源；匹配阶段不能通过词族归一化把 Guard/Guards 等已分别冻结的译名相互覆盖。普通词义冲突通过本条例外表达，不通过自动改词表解决。

## 长输出

通常一次写出整批 result.json。确需分段时，实例在本次交接目录内写出带相同 task_id/input_hash 和不同 ID 的 part 文件，只调用保存操作，不自行校验或合并：

```powershell
& $python $flow --project $project batch-save --session <session> --file <本次目录内part文件>
```

保存操作只按 ID 落盘，不触发模型审查。全部片段保存后直接通知完成，无需再写一份重复的完整结果；主流程 batch-finish 合并并一次检查。重复 ID、批次外 ID、版本不符会拒绝，不按输出顺序对齐。原文不能截断；输入预算不足时明确缩小尚未派发的批次，不动已有草稿。

原 claim/prompt/submit 保留给历史脚本和术语阶段兼容。新正文执行默认使用上述入口，不能混用旧 single_task.py 的 check 循环。
