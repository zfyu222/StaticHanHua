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

reports/full_draft_scan.json 列出所有失败 ID、完整原文、当前译文和具体诊断。程序保留其余译文，跨原批次、跨场景合并失败 ID 为同一轮 translate 修复任务（draft_scan_key、draft_repair_round），沿用正文条数及字符上限。strict_translation 是历史兼容字段，带 draft_scan_key 的整轮修复批次不即时质量验收，而是保存候选并等待全轮复查。通过 batch-open --stage translate 领取这些任务，输入仅包含失败句、当前译文、诊断、必要元数据和相关术语。

一轮是这次复查后生成的全部修复批次。例如第一轮有 10 批：按可用槽位依次完成这 10 批，batch-finish 只做版本、ID、字段类型与锁定检查并保存修复稿，不因术语或占位符问题立即返修。只要有 pending/running/failed 批次，draft-scan 返回 awaiting_round、checked_records=0，不执行质量检查，不生成下一轮；failed 的进程/格式问题需先解决，不能当作该轮完成。最后一批保存回执的 remaining_round_batches=0、next_action=draft-scan --prepare-repairs，才统一复查这 10 批涉及的全部 ID；即使某条提交了相同候选，也在轮末复查。若剩余问题可合为 3 批，则这 3 批组成第二轮，全部完成后再复查。每轮可并发，不要求 10 个实例同时运行。

后续每轮修复完成后仍调用同一个 draft-scan 命令，但程序根据持久化逐条检查记录自动选择范围，不每轮扫描全文：

- initial_full：首次全量验收，汇总全部问题并合批安排修复。
- issue_recheck：整轮所有批次完成后一次验收本轮全部修复 ID，加上其他确实变化的 ID；保留无关通过结果。剩余失败项重新跨批合并为下一轮，禁止逐批检查后插队返修。重复调用不重查未变化且未再次修复的条目。
- final_full：问题全部清零后自动执行一次最终全量验收；若发现遗漏则纳入修复队列，后续继续局部复查，清零后再最终验收。首轮全量无问题时直接沿用该全量结果，不空跑一轮。

reports/draft_checks/round_XXXXX.json 与数据库 draft_check_rounds 保存各轮 phase、scope、checked_ids、passed_ids、具体问题、剩余失败 ID、修复任务状态及验收结果；reports/full_draft_scan.json 是最新汇总，命令只返回紧凑摘要和报告路径。全量验收 scope=full，问题条目复查 scope=issues；不能把局部复查称为全量通过。clean 后才 prepare --stage review，带问题不得绕过进入独立审校。

通过结论绑定原文、候选译文、例外和检查规则；签名比较只识别变化，不对未变化通过项重跑术语/占位符校验。调度或提示词微调不失效；术语变更先比较新旧实际命中，只复查受影响条目；源文或解释数据的全局检查规则变更需要新的全量基线。旧工具已有全量报告时，程序核对保存的扫描回执、输入版本及后续绑定该扫描的严格修复任务，能够证明输入一致就复用基线，将旧报告归档为 legacy_initial_*、execution=reused，只复查扫描后已修复的 ID；没有有效验收记录时才首次全量，不重置或重译。独立语义审校及最终导出仍保持各自门槛。

## 严格阶段与可靠性修复

试译仍即时严格校验。独立审校 review/final_review 已改为 [整轮验收](review_rounds.md)：各批保存候选与证据，全部结束后 review-scan 严格校验，再集中生成下一轮。以下 batch-repair 只用于试译或保存可靠性问题，不用于正文、独立审校或整轮修复的质量返工。正文修复轮末通过 draft-scan 统一严格复查。旧版已被当批检查拒绝的候选，可重新 batch-finish 最新交接保存；有后续或在途交接时不覆盖它，不重置尝试数或真实身份。

结果中可验收的条目持久暂存在 SQLite batch_items；失败条目保留候选文本及具体诊断。完整任务尚未通过时不把该批标记完成。80 条中 2 条失败，78 条不会重新派发。

```powershell
& $python $flow --project $project batch-repair --session <返回repair_required的session> --worker <实际修复实例ID>
# 修复实例只读返回的输入包，写出失败 ID 的结果；结束通知后：
& $python $flow --project $project batch-finish --sessions <修复session>
```

修复包只含失败 ID、完整原文/保护文本、任务原译文、候选译文、诊断、必要元数据及相关术语，不重复背景、通过样例或其他条目。审校修订的 before 对应任务原译文，不是错误候选稿。程序按 ID 合并并执行完整验收；修复后仍失败返回 needs_attention，停止自动重派，交 Sol 判断。每批只自动集中修复一次，仍受原任务/实例/试译预算和租约限制；不能改 ID 或新建任务刷新次数。语法损坏、未知 ID、过期租约等整体错误明确报错，不猜测或丢弃其余内容。

Sol 已判断出具体原因、给出明确处理依据后，可对 needs_attention 的 session 再调用 batch-repair --reason <Sol判断与定向验收要求>。这是有记录的人工主流程判断，不能用于机械循环；仍只携带未解决 ID，保留全部通过项，并消耗原任务剩余尝试次数（默认总共 3 次）及试译预算。无明确原因不允许自动再派。

正文后的程序验收、独立审校及 Sol 最终验收所产生的修复链，按条目累计最多 6 轮常规修复（body_repair_round_limit 默认 6，最多 6）；首次翻译/首次审校不计入，任务格式或传输重试仍受 max_attempts=3 单任务限制。第 6 轮统一复查后仍失败，程序自动生成 repair_tier=sol、required_model=sol_repair_model（默认 gpt-6-sol）、automatic_escalation=true 的失败项任务；直接创建对应模型的新 Sol 子代理（fork_turns=none）执行，不请求用户批准追加轮次。batch-open 回执和输入包携带模型要求，必须按 required_model 派发，不能继续交给 Luna。Sol 修复继续整轮保存、整轮复查及失败 ID 定向处理；相同诊断持续失败时由 Sol 排查原文、术语例外或校验规则根因，不机械重复同一修改，也不借此放宽验收。主对话只调度与检查，不代写译文。Sol 任务使用 codex 后端，API 路线也转交 Sol 子代理，不用原 API 模型冒充升级。既有已完成修复轮及历史计数保留，不重置译文/审校证据，不修改在途任务。锁定冲突及执行故障仍须明确处理，但不得把常规修复超限当作向用户申请追加轮次的理由。

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

派发前读取扫描/sol-round-finish 回执的 repair_dispatch，先按 required_model 创建真实的新实例，再用 batch-open --stage <对应阶段> --worker-model <该模型> --workers <真实实例ID> 领取匹配模型的任务。标准任务使用 gpt-6-luna，Sol 任务使用回执中的精确模型；混合任务池必须传 --worker-model，防止模型领错批次。程序不自行创建代理。

### 防止多术语修复回退

修复包继续携带本句全部命中术语，并以 repair_constraints 区分 satisfied_terms（已有字面命中）、context_exceptions（已有语境说明）和 missing_terms（当前缺失）。仅修必要部分，不能为了一个术语破坏其他术语或将形容词误译成人物名词。字面命中只表示程序约束满足，不代表语义已经审校通过。

已有 exceptions 默认继承，返回时省略或 [] 不清除；补充或修订按 source 合并，撤销必须提供 exception_changes=[{source,action:"remove",reason:"原文依据"}]。程序不判断例外语义为真，仍保留独立审校与 Sol 验收。批次保存、直接 submit、草稿原始候选及逐条身份日志沿用同一合并规则。

仅在整轮统一复查时对比修复前后，记录 regressed_terms 和具体诊断；通过一个术语却丢失另一个也算回退。相同条目在同阶段两次回退后，自动提前进入 Sol 修复队列（required_model），不等 6 轮、不询问追加授权。标准和 Sol 条目分批派发。计数以任务去重，重复读取报告不增加次数；仍只检查本轮影响 ID，不插入当批检查，不扩大到全文。Sol 结合语境排查词义冲突，不允许用删检查或无依据例外提高通过率。
