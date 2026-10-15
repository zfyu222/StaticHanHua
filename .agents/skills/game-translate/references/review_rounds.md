# 独立审校与 Sol 整轮验收

在正文程序验收 clean 后执行。正文修复轮见 batch_execution.md；本文件规定独立审校和最终 Sol 验收的轮次。试译保留原严格协议及预算，不因接入新流程重跑。

## 独立审校轮

用 batch-open --stage review 派发整轮任务，每批默认最多 80 条、一个新 Luna 实例。审校者仍逐条对照完整原文、候选译文、相关术语和元数据，留下六维 checks、真实 source_excerpt、changes 与疑难项。不要把保存候选当作语义验收通过，不运行检查脚本或在每批内往返返修。

batch-finish 只检查版本、ID、字段类型、身份与锁定约束，然后保存该批候选、证据及实际审校者。此时 records 中的正式译文及审校阶段不变化；不能拿候选数宣称全量语义审校通过。完成通知后继续领取本轮剩余任务，不插入质量修复。

```powershell
& $python $flow --project $project review-scan --stage review --prepare-repairs
```

只要本轮还有 pending/running/failed 任务，返回 awaiting_round、checked_records=0，不验收任何批次，也不创建下一轮。全部完成后统一严格检查本轮新候选：语义审校声明及六维证据、原文摘录、修改前后、blocking、术语、例外、变量和结构。通过项才写回记录，失败项跨原任务合并为下一轮审校修复任务；原文、当前译文、候选证据、诊断及相关术语一起交付。

下一轮仍等全部批次完成，再统一 review-scan；已通过且未改变的条目不重审、不改写。新任务继承条目的实际尝试数，不以换批号刷新预算；锁定或达到上限的失败项进入 needs_attention_ids，交 Sol 判断。reports/review_rounds/ 保存逐轮检查 ID、诊断、实际审校实例、通过结果与下一轮任务；最新摘要是 reports/review_round_status.json。

启用润色时 final_review 同样执行 review-scan --stage final_review --prepare-repairs。独立性、证据、术语及导出门槛均保留。旧版已经严格验收的审校结果沿用；旧版最新被当批检查拒绝的候选可重新 batch-finish 保存，不覆盖后续或在途交接、不重置译文。

## Sol 固定范围检查轮

独立审校 clean 后 quality-report --kind final 开启本轮 Sol 检查。范围包括高风险文本、普通抽样及跨批一致性，保留独立审校证据；不是让 Sol 再逐条翻译或审校全文。reports/sol_rounds/ 下归档固定报告，并按 review_batch_records 和 task_input_chars 拆成检查输入包。返回 inspection_manifest；单个超长项不截断，需按原文件完整核对，不能假装已读。

Sol 可以分包检查、分次落盘，但整轮范围与 report_hash 固定。优先读取返回的 next_input_file，它只包含下一包仍待查的项，避免重新阅读已记录内容。所有结论必须实际阅读依据后给出；不由程序自动填充通过。记录结果使用：

```powershell
& $python $flow --project $project sol-round-record --decisions <本次实际检查结论JSON>
```

```json
{"report_hash":"本轮报告hash","reviewer":"sol","summary":"本次实际核对范围及依据","decisions":[{"id":"报告中的检查ID","disposition":"pass","reason":"实际核对依据"},{"id":"某问题检查ID","disposition":"repair","reason":"具体原文依据、错误和验收要求","record_ids":["实际语句ID"]}]}
```

disposition 可为 pass/contextual/repair。一致性组提出 repair 时必须明确列出组内实际受影响语句 ID，不能把整个组或全文自动退回。Sol 主对话不代写译文，只列依据和修订要求。checkpoint-quality --kind final 在本轮打开时也只记录进度，不提前派修。再次 quality-report 返回相同范围的剩余待查视图；已记录的结论不重复进入待查列表，完整范围与证据仍归档。

全部检查完才执行：

```powershell
& $python $flow --project $project sol-round-finish
```

程序拒绝缺少任何本轮结论时关闭或派修，检查期间也拒绝用 reopen-body 逐条插入修复。全部结论齐备后，集中保留通过结论、合并问题 ID，创建一整轮子代理审校修复任务；各批仍只保存候选，必须全部结束再 review-scan。问题处理完成后重新 quality-report，只检查变化条目及受影响的一致性项，通过结论按内容签名复用。

当整轮所有结论都是 pass/contextual 时，sol-round-finish 才批准最终验收。一次提交完整通过文件的 approve-quality --kind final 仍兼容，但必须覆盖整轮且不能绕过已记录的 repair 问题。当前报告已经批准时返回 already_approved，不开始重复检查轮。export 仍执行完整交付门槛；实机检查在回填后完成。
