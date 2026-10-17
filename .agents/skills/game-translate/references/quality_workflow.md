# 自定义流程的质量门槛

适用于 Codex 子代理及自定义 API 路线。主对话管理流程，子代理/API 产生术语、正文及语义审校结果。Sol 是验收和升级任务的模型角色，不表示主对话必然使用 Sol；主对话不是 Sol 时，由新的 Sol 子代理读取程序准备的验收包并给出判断，主对话负责收集及调用程序。程序检查只能证明字段、来源引用和状态满足协议，不能证明语义判断正确；Sol 必须实际阅读证据。

## 独立实例和审校证据

Codex 的 `claim --worker` 使用 spawn_agent 返回的实际 agent ID。每批新建 fork_turns=none 的实例，先创建等待任务的实例，再领取、生成提示词并发送任务；默认验收后结束，下一批换新实例，不把全文划成三个长期对话。不使用 luna-1 等别名，不通过改名或新租约规避独立审校或工作量限制。程序在 `quality_attempts` 和 `quality_authors` 记录身份，拒绝对应产出实例领取或提交审校。提取者不得审自己的术语；翻译者不得审自己的正文；复审者也不得是对应润色者。不同实例可以使用同一个 Luna 模型；不需要把全文语义审校转给 Sol。工作量、局部修复和完整输入预算见 [自定义流程](custom_workflow.md) 的上下文生命周期节。API 每次请求使用新的身份与独立上下文，不携带翻译者的推理或聊天历史。

全文正文审校逐条返回六维 checks：accuracy（含义与主体）、completeness（漏译增译）、terminology、tone、logic（否定/条件/数字/关系）、placeholders。各项含 status=pass/corrected/not_applicable 和具体 evidence。试译的精简协议见下节。非空文本的含义、完整性、逻辑不能标为不适用。source_excerpt 必须是该条原文的连续摘录；reason 解释核对依据。空原文可使用空摘录，并说明检查空译文。

修改译文时同时提供 changes：category（上述维度）、severity=minor/major/critical、source_excerpt、before（任务内完整原译文）、after（完整修订译文）、reason；每个 changes[].source_excerpt 必须是本条原文中的连续摘录，可与顶层或其他修改项引用不同片段，checks 中须标出 corrected 项。未改时 changes=[]。不要机械复写“符合原意”。非空原文 accuracy/completeness/logic 只允许 pass/corrected；短标签没有否定、条件或数字时，仍须核对译文未新增或改变关系，并记录真实依据，不能标 not_applicable。只有其余确实不适用的维度可说明不适用原因。关键疑难项为 blocking，不能伪造通过。

整轮报告 failure_summary 分开统计 review_record_contract、terminology_gate、structure_gate、reported_blocking_semantic_issue，regression_overlap 为重叠标签。统计按首个失败门槛，后续检查可能未执行，不能换算为翻译正确率。修复包 diagnostic_detail 提供错误代码、字段路径、实际值、期望值及修复要求；仅记录协议错误时保留正确译文，由独立审校者核实并补全证据，不自动改状态或补造通过。旧待办任务下次生成输入包时动态补充可识别的字段诊断，不重置任务、身份、尝试数或证据。

术语 decisions.entry_reviews 逐条覆盖分配 ref，包含 evidence_rows、source_excerpt、reason 和 source/translation/category/consistency 四维 checks。语境引用必须来自任务提供的出处。全表 summary 不替代逐项语义检查。冻结决定保留每项证据与实际 review_instances；术语修订也必须提供全表 entry_reviews、review_instances（ref→实际审校实例 ID），经独立审校后由主代理调用 revise-glossary。

## 代表性试译

术语冻结后先执行 pilot_translate、pilot_review，不能直接 prepare translate。默认抽 150 条，不足时全部；按类型、风险与长度兼顾覆盖，不追求全文或每个场景。试译是校准文体和规则，不复制一套昂贵的全文审校过程。每批最多 40 条，150 条默认分成 40/40/40/30，翻译和独立审校各 4 批；pilot_batch_count=1 请求尽量少分批，小样本可单批，不会按场景或角色拆成几十批。完整输入预算仍检查。

默认累计上限 pilot_call_limit=12：翻译、审校、恢复重领和重试合计最多 12 次执行领取。150 条首轮通常需 8 次，余下 4 次供定向修复；上限不因配置/文体变化或任务淘汰而刷新，旧项目仍沿用保存的上限，若改为 12 也保留已用次数。旧运行的历史消费单独保留，从首次启用预算政策开始计数。该计数是任务执行次数，不是底层模型消息数；一次执行内禁止轮询式聊天和长时间反复工具调用。派发前调用 pilot-plan，预算或输入检查不通过就停止创建实例；不要让额外 Sol 子代理调度或监视试译，程序负责等待和机械检查，Sol 只做最终验收及必要问题判断；主对话非 Sol 时可创建验收实例，但不创建额外调度或监视实例。

pilot_review 优先使用精简 JSON：顶层 review_summary 一次说明六维核对，各项含 status、evidence。只要本批有非空原文，批总结 accuracy/completeness/logic 只允许 pass/corrected；详细记录的三项也遵守此限制及 changes 摘录须来自本条原文的约束。审校者仍逐条对照全部分配 ID，无问题记录仅返回 `{id, reviewed:true, verdict:pass}`。已有术语例外也须逐项核对，使用 verdict:corrected 及完整证据，即使译文无需改动；此时 changes=[]，不能用精简 pass 隐藏例外。修改或疑难记录返回 verdict:corrected、完整译文、reason、source_excerpt、六维 checks、changes、issues 和必要 exceptions。直接 submit 和批次交接均继承例外，均支持按 ID 的 review_summaries（优先于全批 review_summary）；批总结不能替代逐条检查，缺失 ID 会拒绝提交。Sol 报告中相同批总结只出现一次。

既有项目保持已选样本及完成结果，不因升级重跑。需要重新合批时先暂停并结束/回收在途任务，再通过 configure 调整 pilot_batch_count=1、pilot_call_limit=12，prepare 仅准备未完成语句。用户希望进一步降低样本量时，可用 `reduce-pilot --records 30 --reason <降低校准成本>` 复用已译样本；不要在有在途实例时改样本。

quality-report --kind pilot 在分配前生成完整输入预算内的 inspection_manifest 和 next_input_file。主流程只交接 next_input_file，不把整份报告交给实例自行拆分；每包包含本包原文、译文、必要批总结、文体背景、覆盖统计及待填写的输出模板。按包保存通过检查后重新请求报告，程序复用已通过项并给出下一包；最终 approve-quality 仍要求完整覆盖。单个不可拆检查仍超限时，在生成任务阶段报错，不截断证据。

Sol 检查覆盖报告。缺少场景、角色元数据时记录缺失，必要时补提取上下文或人工选择样本；不要把“抽到了长短句”当作已覆盖全部剧情和口吻。

Sol 发现局部问题时，先将已实际检查通过的项保存为 decisions 文件（report_hash、reviewer=sol、summary、decisions=[{id,disposition:pass/contextual,reason}]），调用 `checkpoint-quality --kind pilot --decisions <文件>`。允许只提交已通过项；这只保存检查进度，不批准整个阶段。再按 ID 退回问题语句，修复后重新 quality-report：原文、译文、审校证据未变且未被列为规则变更影响范围的检查复用结论，只列变化的语句及受影响的同源一致性/角色口吻组；未检查项仍保留待查。不能为未实际阅读的项生成通过记录。术语按命中差异限定范围，提示词微调默认保留旧结论，不因全局版本号变化强制重新检查无关语句；最终仍通过 approve-quality 验收当前报告。

```powershell
& $python $flow --project $project pilot-plan
& $python $flow --project $project prepare --stage pilot_translate
# 独立翻译实例执行并 submit 所有批次
& $python $flow --project $project prepare --stage pilot_review
# 不同审校实例执行并 submit 所有批次
& $python $flow --project $project quality-report --kind pilot
```

输出 reports/pilot_quality_review.json 和 pilot_sol_decisions.template.json。Sol 实际阅读试译全文、逐条审校证据和术语变更，确定含义、口吻、UI 简洁性、专名及复杂句处理是否满足要求。将模板另存为完成的决定文件，保留 report_hash，reviewer=sol；summary 写通过的文体规则及覆盖限制。每个 required_decisions 恰好一个决定：disposition=pass，或有具体语境依据的 contextual，reason 不为空。不能用脚本自动填满“通过”。

```powershell
& $python $flow --project $project approve-quality --kind pilot --decisions <Sol实际审查决定JSON>
```

试译验收后，记录恢复试译前的生产状态；正式正文重新翻译，带最多五条通过的试译样例及文体总结。试译和实际审查文件归档在 reports/accepted/。术语只使命中变化的样本进入复查，其他试译结果及结论保留；若未影响样本，沿用试译验收。提示词、文体和背景微调默认不重新试译；局部影响用 configure --impact ids --ids 指定，实质改变全文要求才 --impact all。样本配置调整仍复核新样本，但复用已有结果。沿用验收会记录来源，不把旧结论伪装成新一轮人工检查。

## Sol 遗留争议与难题裁决

全文审校以收敛明确错误为目标。首轮独立子代理完整对照每条原译文，保留六维证据；含义准确、自然可读且符合冻结术语和既定文体即可通过。同义替换、个人偏好和另一种正确译法不列为错误或 warning，不为此返工。polish 默认关闭，不因正文完成而自动启动全文润色。

翻译实例只翻译，不兼任自审、不运行检查脚本。全文翻译和独立审校默认均每批最多 80 条；试译仍每批最多 40 条并即时严格验收。正文翻译先仅校验版本、ID、字段类型及锁定约束，完整保存草稿。全部草稿完成后，draft-scan --prepare-repairs 做首次全量验收并将失败 ID 跨批合并修复；修复批次仅保存候选，不逐批质量验收或插入返修；本轮所有批次完成后统一复查全部本轮修复条目，再将剩余失败项合批为下一轮，保留未变化的通过结果，不每轮重扫全文或重译通过项。问题清零后自动最终全量验收；无问题的首次全量结果可直接复用。逐轮明确区分 full 全量与 issues 局部复查，记录范围、问题、修复状态和验收结果，最终 clean 才允许准备独立审校。例外语义仍由独立审校复核，导出仍严格检查全库，不忽略遗漏。正文执行用 batch-open / batch-finish 事件交接，记录与来源见 [批次程序交接](batch_execution.md)。

独立审校改为整轮保存和验收：review/final_review 各批仍逐条语义核对并输出六维证据，batch-finish 只保存候选，不当批质量返修。全部批次完成后，运行 review-scan --stage review --prepare-repairs（润色复审用 final_review），统一严格检查证据和译文；通过项落库，失败 ID 跨原批次合并为下一轮。已通过项保留，下一轮也必须全部完成再复查。程序保存候选不代表语义验收通过，未清零不能进入 Sol 验收或导出。

Sol 最终验收按固定范围整轮收集，详细协议和样例见 [独立审校与 Sol 整轮验收](review_rounds.md)：

```powershell
& $python $flow --project $project quality-report --kind final
# 按返回 inspection_manifest 分包实际阅读，并分次保存本轮结论
& $python $flow --project $project sol-round-record --decisions <本次实际检查结论JSON>
# 本轮全部检查齐备后，才集中派修或批准
& $python $flow --project $project sol-round-finish
```

正文后 Sol 是高质量、高成本的定向难题处理者。最终报告只将独立审校明确遗留的 issues 交给 Sol 裁决；数字、否定、条件、变量、长句、术语例外和历史改动只是独立审校的提示，不自动升级；普通抽样也不交 Sol。coverage.risk_records 只是风险统计，不是派发量，实际看 coverage.sol_records 和 remaining_checks。试译代表性样本的 Sol 验收保持不变。源文与真实审校证据保留，负责验收的 Sol 实例只裁决，不代写译文。

同源多译和角色口吻分组本身不是争议，不自动产生 Sol 检查项。独立审校发现确切冲突时须按具体 ID 留下原文依据与未解决 issues，才进入难题裁决；不得将整个组或全文转给 Sol，也不能在本轮逐条插入修订。

Sol 用 pass/contextual 记录通过，repair 写明原文依据、错误、验收要求及确切受影响语句 ID。每个本轮检查 ID 都要有结论；有任何未检查项时程序拒绝关闭或派修。检查期间 checkpoint-quality --kind final 也仅保存进度，reopen-body 不得用于逐条抢先返修。本轮范围与 hash 固定，重复 quality-report 只提供剩余待查内容；完整证据和分包归档于 reports/sol_rounds/。

本轮结束后，Sol 问题统一合批为子代理审校修复任务；全部批次完成再 review-scan。通过结论按内容签名复用，下一轮只裁决仍然遗留的具体争议；独立复查通过的修订不再触发 Sol。旧大范围轮已有的问题作为 carried_findings 保留并交修复，不重读、不静默忽略。无争议无问题时通过 sol-round-finish 完成质量闭环，不创建模型审查任务或编造逐项通过。仍有 carried_findings 时 approve/export 拒绝放行。

正文后的常规修复按条目累计最多 6 轮，之后自动生成 Sol 子代理修复任务，不询问追加轮次。任务执行重试仍使用 max_attempts；修复轮计数独立保存，不通过换任务 ID 刷新，不自动重启全量审校。试译仍使用原预算和定向 reopen-body/checkpoint-quality 协议；已接受试译不因正文流程升级重跑。

```powershell
# 若上一轮派发了修复，完成全部 review/final_review 批次后：
& $python $flow --project $project review-scan --stage review --prepare-repairs
# 存在剩余问题继续整轮修复；clean 后重新开始 Sol 剩余检查轮
& $python $flow --project $project quality-report --kind final
# 记录本轮全部实际结论并 sol-round-finish 通过后：
& $python $flow --project $project export
```

报告及结论绑定当前原文、术语、译文、审校证据与规则。过期或范围外的结论拒绝接收；已批准且数据未变化时不重复开启检查轮。导出仍完整校验，并记录 in_game_check=pending_repack；实机检查在回填后完成。

## 回填后的游戏内检查

export 生成 reports/in_game_check.template.json，由 game-repack 在真实游戏验证后填写：实际游戏版本、平台、当前 translation_sha256、载荷文件、证据文件，以及 fonts/layout/ui/dialogue/quest 五类检查。

- fonts：中文、标点和特殊字符显示正常，无缺字、乱码。
- layout：长句、换行、选项、按钮和提示正常，无遮挡或截断。
- ui：菜单、设置、关键按钮含义及实际操作对应。
- dialogue：代表性对白和分支中称谓、上下文、人物口吻合理。
- quest：任务目标、条件、数量、选项及推进结果对应。

每项包含 status=pass、实际 scene 和具体 notes。dialogue/quest/ui 不在本次范围时可用 not_applicable 并解释；字体和排版必须实测通过。无法启动游戏、未走到所需场景或仍有问题时保持 pending/fail，不能调用程序记录为通过。

payload_files 每项为 `{path:项目内载荷路径,game_path:游戏根目录安装路径,sha256:实际哈希}`；evidence_files 每项为 `{path:项目内截图或测试记录,sha256:实际哈希}`，各至少一个。不能指向 Game 链接或项目外部；先保存补丁副本和证据到项目中。哈希绑定本次实测文件，更新载荷后必须重新检查。

```powershell
& $python $flow --project $project record-game-check --file <实际检查JSON>
& $python $flow --project $project release-check
```

保存 reports/in_game_check.json。release-check 校验当前 Sol 验收、正式 CSV、实测载荷及证据哈希。发布程序对已初始化的自定义项目要求检查通过，且打包的每个文件、游戏相对安装路径、版本和平台与实测一致；dry-run 仅预览，不能替代验证。README 分别记录文本验收和实机验收，不承诺未测场景。

## 旧运行升级及交付

旧数据库没有 quality_version=2 时拒绝继续正文。先 status、暂停并结束/回收在途任务，再 configure 提交配置补丁；升级保留历史文件，但重置正文状态与术语冻结，重新完成有实例来源和逐项证据的术语、试译、正文流程。锁定译文需先明确处理，不静默解锁。

适合入库：本参考所述配置、术语证据、已验收报告/决定、最终报告及实机验证记录与必要截图。运行数据库、任务、完整批次结果、缓存和备份仍不入库。质量门槛由自定义程序执行，AINiee GUI 路线保持原有选择。

### 防止多术语修复回退

修复包继续携带本句全部命中术语，并以 repair_constraints 区分 satisfied_terms（已有字面命中）、context_exceptions（已有语境说明）和 missing_terms（当前缺失）。仅修必要部分，不能为了一个术语破坏其他术语或将形容词误译成人物名词。字面命中只表示程序约束满足，不代表语义已经审校通过。

已有 exceptions 默认继承，返回时省略或 [] 不清除；补充或修订按 source 合并，撤销必须提供 exception_changes=[{source,action:"remove",reason:"原文依据"}]。程序不判断例外语义为真，仍保留独立审校与 Sol 验收。批次保存、直接 submit、草稿原始候选及逐条身份日志沿用同一合并规则。

仅在整轮统一复查时对比修复前后，记录 regressed_terms 和具体诊断；通过一个术语却丢失另一个也算回退。相同条目在同阶段两次回退后，自动提前进入 Sol 修复队列（required_model），不等 6 轮、不询问追加授权。标准和 Sol 条目分批派发。计数以任务去重，重复读取报告不增加次数；仍只检查本轮影响 ID，不插入当批检查，不扩大到全文。Sol 结合语境排查词义冲突，不允许用删检查或无依据例外提高通过率。

审校批量还受预计输出预算限制，Sol 升级后也有累计轮数与无进展停止条件；跨阶段/跨 revision 计数及恢复边界见 [整轮验收](review_rounds.md#修复计数与停止条件)。

任务输入和输出模板统一在派发前由程序准备；独立审校者仍负责真实语义核对，机器校验与结果合并由程序执行。详见 [统一职责](custom_workflow.md#程序准备任务子代理专注本职)。
