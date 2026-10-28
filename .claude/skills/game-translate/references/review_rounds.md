# 独立审校与标准档整轮验收

在正文程序验收 clean 后执行。正文修复轮见 batch_execution.md；本文件规定独立审校和最终标准档验收的轮次。本文件只用于正式译文审校；试译由主 AI 整体校准，不进入本修复链。

## 独立审校轮

用 batch-open --stage review 派发整轮任务，每批默认最多 80 条、一个新便宜档实例。审校者仍逐条对照完整原文、候选译文、相关术语和元数据，留下六维 checks、真实 source_excerpt、changes 与疑难项。不要把保存候选当作语义验收通过，不运行检查脚本或在每批内往返返修。

batch-finish 只检查版本、ID、字段类型、身份与锁定约束，然后保存该批候选、证据及实际审校者。此时 records 中的正式译文及审校阶段不变化；不能拿候选数宣称全量语义审校通过。完成通知后继续领取本轮剩余任务，不插入质量修复。

任务分配阶段会计算完整输入并自动拆批；旧审校任务需要显式重新分配时，可用 `batch-split --task <task_id>` 按完整输入包及预计输出预算拆分。该命令只接受当前 revision 中未尝试、无交接、仍待办的 review/final_review 首轮或修复任务；它保留每个失败 ID、诊断、证据、修复约束和计数，先确认所有子包都在预算内，再将原任务标记为 obsolete 并创建子任务。回执会列出各子包的 ID 数和 Token 数；不截断证据、不重跑已完成条目。已领取或有交接记录的任务不能拆分。

```powershell
& $python $flow --project $project review-scan --stage review --prepare-repairs
```

只要本轮还有 pending/running/failed 任务，返回 awaiting_round、checked_records=0，不验收任何批次，也不创建下一轮。全部完成后统一严格检查本轮新候选：语义审校声明及六维证据、原文摘录、修改前后、blocking、术语、例外、变量和结构。通过项才写回记录，失败项跨原任务合并为下一轮审校修复任务；原文、当前译文、候选证据、诊断及相关术语一起交付。

下一轮仍等全部批次完成，再统一 review-scan；已通过且未改变的条目不重审、不改写。常规修复与任务重试分开计数；最多 6 轮后仍失败自动升级新的标准档子代理，不再请求追加轮次。只有锁定冲突等实际阻塞进入 needs_attention_ids。reports/review_rounds/ 保存逐轮检查 ID、诊断、实际审校实例、通过结果与下一轮任务；最新摘要是 reports/review_round_status.json。

修复轮与首轮沿用同一已核实的 game_background 和 style，不能因进入修复而丢失题材背景。新修复任务保存背景，旧修复任务重新生成输入包时从当前已验证运行补充背景，不改写任务哈希或在途输入；补充后的完整输入仍受预算和 batch-split 检查。语义 reason、issues[].reason 及例外撤销理由必须是非空字符串，null、数字或对象不能当作审校依据。空原文或仅含空白的原文可以使用空 source_excerpt，实际换行/控制符保护仍严格检查。

启用润色时 final_review 同样执行 review-scan --stage final_review --prepare-repairs。独立性、证据、术语及导出门槛均保留。旧版已经严格验收的审校结果沿用；旧版最新被当批检查拒绝的候选可重新 batch-finish 保存，不覆盖后续或在途交接、不重置译文。

## 标准档固定范围检查轮

独立审校 clean 后 quality-report --kind final 只收集审校结果中明确遗留的 issues，供标准档裁决。标准档定位为高质量、高成本的难题处理者，不能按数字、否定、变量、术语例外、历史改动、普通抽样、同源多译或角色口吻分组自动扩大范围；这些不是未解决问题。已修正且通过独立复查的记录不再进入标准档。reports/standard_rounds/ 归档固定报告和争议输入包，返回 inspection_manifest。实际仍失败的条目按下方多轮升级机制交新标准档子代理定向修复；主对话不承担大批逐项复审。

旧大范围标准档轮在下一次 quality-report 时自动 supersede；保留原始报告、实际通过结论和已发现的问题。通过结论按未变的内容签名复用；已有 repair 结论成为 carried_findings，交下一轮定向修复，不让标准档重读、不丢弃或自动批准。新 report_hash 拒绝旧输入继续落盘。carried_findings 不为空时不能直接 approve/export；全部争议结论齐备后用 standard-round-finish 集中派修，任务持久化后才移交该问题责任。没有争议也没有遗留问题时 remaining_checks=0、next_action=standard-round-finish，程序关闭质量阶段，不需要编造逐项通过记录。试译使用独立的主 AI 整体校准流程。

标准档可以分包检查、分次落盘，但整轮范围与 report_hash 固定。优先读取返回的 next_input_file，它只包含下一包仍待查的项，避免重新阅读已记录内容。所有结论必须实际阅读依据后给出；不由程序自动填充通过。记录结果使用：

```powershell
& $python $flow --project $project standard-round-record --decisions <本次实际检查结论JSON>
```

```json
{"report_hash":"本轮报告hash","reviewer":"standard","summary":"本次实际核对范围及依据","decisions":[{"id":"报告中的检查ID","disposition":"pass","reason":"实际核对依据"},{"id":"某问题检查ID","disposition":"repair","reason":"具体原文依据、错误和验收要求","record_ids":["实际语句ID"]}]}
```

disposition 可为 pass/contextual/repair。一致性组提出 repair 时必须明确列出组内实际受影响语句 ID，不能把整个组或全文自动退回。标准档主对话不代写译文，只列依据和修订要求。checkpoint-quality --kind final 在本轮打开时也只记录进度，不提前派修。再次 quality-report 返回相同范围的剩余待查视图；已记录的结论不重复进入待查列表，完整范围与证据仍归档。

全部检查完才执行：

```powershell
& $python $flow --project $project standard-round-finish
```

程序拒绝缺少任何本轮争议结论时关闭或派修，检查期间也拒绝用 reopen-body 逐条插入修复。全部结论齐备后，集中保留通过结论、合并问题 ID 和 carried_findings，创建一整轮子代理审校修复任务；各批仍只保存候选，必须全部结束再 review-scan。问题处理完成后重新 quality-report，只处理仍然明确遗留的争议，通过结论按内容签名复用；不能仅因译文发生过修改而重派标准档。

当整轮所有结论都是 pass/contextual 时，standard-round-finish 才批准最终验收。一次提交完整通过文件的 approve-quality --kind final 仍兼容，但必须覆盖整轮且不能绕过已记录的 repair 问题。当前报告已经批准时返回 already_approved，不开始重复检查轮。export 仍执行完整交付门槛；实机检查在回填后完成。

用户明确授权少量遗留由主对话直接处理时，可用 `quality-round-record-direct --decisions <JSON>` 记录至多16项的实际裁决。reviewer须使用真实 `main:` 身份，并写入 user_authorization；report_hash、覆盖范围、逐项理由及整轮关闭/派修门槛不变，不能冒充标准档。`restore-review-layout --file <JSON>` 仅用于暂停、无在途任务时的至多8条已失败候选：绑定源文/候选哈希，只恢复相同段落数量下的换行类型及边界空白，保留原始回复、修复前审校项和累计次数；另记人工格式复核归属。候选回到pending后仍须review-scan完整验收，不能直接标记通过。

## 正文后自动升级

正文后的程序验收、独立审校及标准档最终验收所产生的修复链，按条目累计最多 6 轮常规修复（body_repair_round_limit 默认 6，最多 6）；首次翻译/首次审校不计入，任务格式或传输重试仍受 max_attempts=3 单任务限制。第 6 轮统一复查后仍失败，程序自动生成 repair_tier=standard、required_tier=standard、automatic_escalation=true 的失败项任务；直接按[模型档位](../SKILL.md#模型档位)创建不继承主对话上下文的新标准档子代理执行，不请求用户批准追加轮次。batch-open 回执和输入包携带模型要求，必须按 required_tier 派发，不能继续交给便宜档。标准档修复继续整轮保存、整轮复查及失败 ID 定向处理；相同诊断持续失败时由标准档排查原文、术语例外或校验规则根因，不机械重复同一修改，也不借此放宽验收。主对话只调度与检查，不代写译文。标准档任务使用 codex 后端，API 路线也转交标准档子代理，不用原 API 模型冒充升级。既有已完成修复轮及历史计数保留，不重置译文/审校证据，不修改在途任务。锁定冲突及执行故障仍须明确处理，但不得把常规修复超限当作向用户申请追加轮次的理由。

派发前读取扫描/standard-round-finish 回执的 repair_dispatch，先按 required_tier 创建真实的新实例，再用 batch-open --stage <对应阶段> --worker-tier <该档位> --workers <真实实例ID> 领取匹配档位的任务。便宜档任务用当前 Agent 工具的便宜档模型，标准档任务用标准档模型（见[模型档位](../SKILL.md#模型档位)）；混合任务池必须传 --worker-tier，防止领错批次。程序不自行创建代理。

### 防止多术语修复回退

修复包继续携带本句全部命中术语，并以 repair_constraints 区分 satisfied_terms（已有字面命中）、context_exceptions（已有语境说明）和 missing_terms（当前缺失）。仅修必要部分，不能为了一个术语破坏其他术语或将形容词误译成人物名词。字面命中只表示程序约束满足，不代表语义已经审校通过。

已有 exceptions 默认继承，返回时省略或 [] 不清除；补充或修订按 source 合并，撤销必须提供 exception_changes=[{source,action:"remove",reason:"原文依据"}]。程序不判断例外语义为真，仍保留独立审校与标准档验收。批次保存、直接 submit、草稿原始候选及逐条身份日志沿用同一合并规则。

术语字面未命中不是最终语义判错。返修及审校输入的 terminology_findings 逐项列出 source 和 expected_translation；模型可以修正，也可以坚持现译并逐项写 exceptions=[{source,reason}]，理由须说明具体原句语境及现译为何正确。程序检查结构和关联性后接受并持久保存本句结论；以后扫描/修复/审校/导出不再仅因同一字面译名缺失而否定它。独立审校仍核对其他含义和已有结论，只有发现具体反证才显式撤销；不要求为了让字面检查通过而改坏正确译文。空理由或仅返回相同译文不产生通过记录，术语结论不替代占位符和结构验收。

API 展示及回复使用本批短编号：terminology_findings=[{term_ref:"t1"}]，对应 shared_terms.t1 中的原术语和推荐译名；模型返回 exceptions=[{term_ref:"t1",reason:"原句语境依据"}]，不抄术语原文。撤销已有例外使用本条 existing_exceptions 的 exception_ref（x编号），允许撤销当前未命中的旧例外。session 映射将编号绑定到具体记录的 term_refs，禁止引用同批其他记录独有的术语或未知、重复编号；空/非字符串理由和编号与 source 混填会触发格式错误。程序恢复 source 后再交共同收集和语义验收，数据库及历史继续保存完整术语与原因。已有例外默认继承，模型无需重复抄写；子代理的完整 source 协议不变。

API 独立审校与润色后复审未改译文返回 keep:true；改动只返回一次完整 translation，changes 每项只输出 category/severity/source_excerpt/reason。程序依据对应 session 的任务原译及本次新译恢复 before/after，并绑定 reviewed 标记；明确提供的错误 before/after 不覆盖。六维 checks、摘录、原因、changes 和 issues 仍按共同协议验收，keep:true 不免除审校证据，也不表示自动通过。polish 同样支持 keep:true，但不因此增加六维审校字段。保存的完整结果继续使用原协议。

仅在整轮统一复查时对比修复前后，记录 regressed_terms 和具体诊断；通过一个术语却丢失另一个也算回退。相同条目在同阶段两次回退后，自动提前进入标准档修复队列（required_tier），不等 6 轮、不询问追加授权。便宜档和标准档条目分批派发。计数以任务去重，重复读取报告不增加次数；仍只检查本轮影响 ID，不插入当批检查，不扩大到全文。标准档结合语境排查词义冲突，不允许用删检查或无依据例外提高通过率。

## 修复计数与停止条件

普通修复按语句 ID 跨 translate、review、final_review 和所有 revision 累计，最多 6 轮后升级标准档；同一任务的格式/传输重试不增加修复轮数。修复任务标记 repair_budget_scope=record。历史任务和旧阶段计数作为下限保留，已完成任务即使被标记 obsolete 也计入，不因 configure、拆批或换阶段刷新。

标准档修复默认累计最多 3 轮（standard_repair_round_limit），同一条目连续 2 轮标准档检查得到相同的未解决诊断（standard_no_progress_limit）也停止该条自动派发。停止按条目生效，其他条目继续；通过项不返工。报告返回 needs_attention_ids、repair_stop_reasons（standard_round_limit / standard_no_progress），保留候选、证据及计数，不能批准或导出未解决问题。主流程应核对原文、术语语境和校验规则根因，不通过改任务 ID 或调整配置重置预算。配置修改不清除停止历史；确有规则缺陷应修正规则并复核保存的候选，通过后再继续，不能机械追加模型轮次。

## 审校输出预算

review_batch_records=80 与 review_batch_tokens=1024 共同约束批次（原文、译文和问题描述计入 Token）；条数是独立上限，审校不再按翻译条数直接合批。review_output_tokens 默认 16384；API 与 SubAgent 均使用该共同预算，再预留 20%。API 容量不足时派发前报错，不按后端缩小批次；容量配置见 [API 执行](custom_workflow.md#api-执行)。程序根据原译文、可能的完整前后修订、摘录和六维证据估计输出，按预算提前拆批。估算不是精确 tokenizer，不保证模型绝不截断；不得为满足预算截断原文或审校证据。

正文审校、复审及其修复采用相同预算与逐条完整证据估算；试译不执行独立审校，pilot-plan 只估计翻译任务和输入预算。单条超限时在创建任务前明确报错，需调整输出额度或设计保留 ID 对齐的切分。完整输入和预计输出预算统一在分配阶段检查并自动拆批，claim/batch-open 领取时不再重算或校验 Token/输出预算。prepare 会按同一规则重新分配尚未尝试的旧待办任务；可用 batch-split 显式拆分当前 revision 中尚未尝试、无交接的 review/final_review 任务（首轮和修复均支持），同时满足输入与输出预算，保留 ID、修复计数及证据。已领取的任务不能拆分。

标准档裁决输入包同样先完成完整文件大小检查和自动分包，附有 report_hash 与检查 ID 预填的输出模板；单项超限在分配时停止，不生成 over_budget=true 的包。已保存的裁决不重做，next_input_file 同时过滤已完成检查和对应模板项。

新任务在批次收集时先作逐条协议检查（含必需维度状态、真实非空证据字段、连续摘录和改动字段关系），只返修协议失败 ID；正式语义、术语、保护结构及 blocking 的整轮验收仍等本轮全部批次完成。API checks 的 [status,evidence] 只是无损传输表示，仍须逐维真实判断。

## 同条审校证据去重与机械格式

同一记录真实依据重叠时可写 evidence={e1:具体文本,...}，reason、各维 evidence 和改动 reason 显式引用 {ref:e1}；六维状态仍由模型逐项给出，引用只能在本条内展开。未知/空/嵌套引用不成立，不因省略证据推定通过。完整结果恢复为原证据结构后执行原验收。

layout_policy=exact_v1 的新任务可由程序按绑定源文恢复首尾横向空白和相同段落数下的换行编码，留下 normalize_layout 审计。原始回复保留，语义证据/状态不由程序补写；锁定、段落数量不同、缺失标记、明确格式改动或不一致修改后文本仍交原失败修复流程。旧在途任务不改变。
