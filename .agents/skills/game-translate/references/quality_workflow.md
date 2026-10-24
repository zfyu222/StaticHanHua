# 自定义流程的质量门槛

适用于 Codex 子代理及自定义 API 路线。主对话管理流程，子代理/API 产生术语、正文及语义审校结果。Sol 是验收和升级任务的模型角色，不表示主对话必然使用 Sol；主对话不是 Sol 时，由新的 Sol 子代理读取程序准备的验收包并给出判断，主对话负责收集及调用程序。程序检查只能证明字段、来源引用和状态满足协议，不能证明语义判断正确；Sol 必须实际阅读证据。

## 独立实例和审校证据

Codex 的 `claim --worker` 使用 spawn_agent 返回的实际 agent ID。每批新建 fork_turns=none 的实例，先创建等待任务的实例，再领取、生成提示词并发送任务；默认验收后结束，下一批换新实例，不把全文划成三个长期对话。不使用 luna-1 等别名，不通过改名或新租约规避独立审校或工作量限制。程序在 `quality_attempts` 和 `quality_authors` 记录身份，拒绝对应产出实例领取或提交审校。提取者不得审自己的术语；翻译者不得审自己的正文；复审者也不得是对应润色者。不同实例可以使用同一个 Luna 模型；不需要把全文语义审校转给 Sol。工作量、局部修复和完整输入预算见 [自定义流程](custom_workflow.md) 的上下文生命周期节。API 每次请求使用新的身份与独立上下文，不携带翻译者的推理或聊天历史。

全文正文审校逐条返回六维 checks：accuracy（含义与主体）、completeness（漏译增译）、terminology、tone、logic（否定/条件/数字/关系）、placeholders。各项含 status=pass/corrected/not_applicable 和具体 evidence。这些逐条证据要求用于正式审校；试译采用下述整体校准评估。非空文本的含义、完整性、逻辑不能标为不适用。source_excerpt 必须是该条原文的连续摘录；reason 解释核对依据。空原文可使用空摘录，并说明检查空译文。

修改译文时同时提供 changes：category（上述维度）、severity=minor/major/critical、source_excerpt、before（任务内完整原译文）、after（完整修订译文）、reason；每个 changes[].source_excerpt 必须是本条原文中的连续摘录，可与顶层或其他修改项引用不同片段，checks 中须标出 corrected 项。未改时 changes=[]。不要机械复写“符合原意”。非空原文 accuracy/completeness/logic 只允许 pass/corrected；短标签没有否定、条件或数字时，仍须核对译文未新增或改变关系，并记录真实依据，不能标 not_applicable。只有其余确实不适用的维度可说明不适用原因。关键疑难项为 blocking，不能伪造通过。

整轮报告 failure_summary 分开统计 review_record_contract、terminology_gate、structure_gate、reported_blocking_semantic_issue，regression_overlap 为重叠标签。统计按首个失败门槛，后续检查可能未执行，不能换算为翻译正确率。修复包 diagnostic_detail 提供错误代码、字段路径、实际值、期望值及修复要求；仅记录协议错误时保留正确译文，由独立审校者核实并补全证据，不自动改状态或补造通过。旧待办任务下次生成输入包时动态补充可识别的字段诊断，不重置任务、身份、尝试数或证据。

所有分配语句都要先按原文确定施事/受事、修饰对象和语境义，再对照译文；不能先为旧译找理由。`stubbled scalp`与`chin ... stubble`分别对应头皮短发茬和下巴胡茬，把前者搬成“胡茬的头皮”是accuracy问题，不只是文风；原文明说`scalp ... beard`则保留其奇异设定。此法同样用于其他对象、动作、物理/比喻义及常用词，不是只搜“胡茬”或禁止特定中文词。异常搭配需要回查原文，原文不支持时不能用奇幻背景开脱，原文明说时也不能按现实常识抹除。正确译文保持。判断和证据仍由本轮模型提供，不新增额外字段、API轮次或全文释义，也不让用户先读全文挑句子；提示强化不能保证语义零漏判。

术语 decisions.entry_reviews 逐条覆盖分配 ref，包含 evidence_rows、source_excerpt、reason 和 source/translation/category/consistency 四维 checks。语境引用必须来自任务提供的出处。全表 summary 不替代逐项语义检查。冻结决定保留每项证据与实际 review_instances；术语修订也必须提供全表 entry_reviews、review_instances（ref→实际审校实例 ID），经独立审校后由主代理调用 revise-glossary。

## 可选的原文独立解读参考

这是辅助诊断工具，不是全自动流水线必需步骤，不能要求用户人工阅读全文筛选。它不是默认增加的一轮翻译，也不是自动发现全部语义错误的方法。仅使用此工具时才明确需要复查的ID，控制来源生成和重审的各自请求上限。另一个隔离项目按既有流程生成这些完整原文的API `pilot_translate` 草稿；其输入不含旧译、候选、逐条元数据、已批准译例或其他翻译上下文。此阶段仅产出未审定参考，不代表其试译已被主AI接受，也不自动进入该副本的全文翻译。

来源生成完成、无请求在途后，暂停目标项目并导入：

```powershell
& $python $flow --project $project import-review-readings --source-project '<独立原文项目绝对路径>' --ids '<目标ID1>' '<目标ID2>' --reason '核对指定记录的词义分歧'
```

导入器只读来源库，核对完成的API试译、实际收到请求结果、源文件哈希、任务/结果绑定、完整原文和当前保存草稿一致。拒绝带旧译/候选/逐条元数据的来源；不能把手工填入的正确答案冒充独立API读法。它不生成草稿、不调用API、不修改当前译文。生成参考的请求与Token记录仍在来源项目，不占用目标项目的请求计数，因此应分别限制并汇总两侧费用，不能声称额外核对免费。

只接受显式ID、不同项目、精确原文对应和未锁定记录；目标须暂停、无未完成任务/请求，所选条目同处于草稿完成的translate或整轮完成的review阶段。translate阶段供后续正常审校读取；review阶段只将所选条目加入下一轮，旧通过项、尝试历史和修复预算保持。达到既有停止条件则拒绝并回滚，不借导入刷新次数。更后阶段暂不支持此入口，不为套用参考而自动重启全文。

`independent_source_reading`在共用任务分配前加入、绑定原文哈希，并计入完整输入及预计输出预算；API和SubAgent使用同一语义。既有在途输入不改写，证明信息与本地路径不发给模型。审校者须自行对照原文，允许否定参考，保留正确原译；完整六维证据、来源和整轮验收继续执行，程序不从参考或文字理由拼造正式译文。

2026-10-24小样本中，两处真实“胡茬的头皮”通过该入口变为短发茬，其余四条未变；同一当前API在不带参考的新对照中也修正三条错误、保留三条正确译文。因此仅验证入口和本组样本可行，不能归因于参考、估计全游戏成功率或默认增加成本。详情见[真实验证报告](../../../../Tools/localization/reports/review_optimization_20261024.md)。

## 随机样本试译

试译判断方案是否适合用于全文：流程能否稳定运行、文体是否符合预期、错误是否普遍或集中于某类情况、是否要调整提示词、模型或基础参数。主 AI 以诊断和调优为目的阅读原译文，不承担逐句精修。个别错误可以留给正式独立审校，不要求 150 条零错误。

1. 全文术语审校并冻结后，从全文记录中不放回随机抽取默认 150 条，不足时全部；保存选中 ID 供前后对比。没有分类、风险配额或覆盖补齐步骤。既有样本和用户指定样本保留。
2. 执行 pilot_translate，使用正式正文相同的拆批、提示词、保护、解析与草稿保存机制。全部批次结束后调用 pilot-report。程序保留原始结果并汇总占位符、术语、空译文等机械诊断，不自动派发修复，不把机械报错比例当作语义错误率。
3. 主 AI 阅读 reports/pilot_calibration.json 的原文、译文、诊断和运行任务情况，判断流程、风格和总体错误表现。无需逐条六维证据或固定检查清单；评价语义错误比例时说明实际核对范围，不能编造全文正确率。未完成或执行失败的任务必须先处理，不能批准为链路已跑通。
4. 用 pilot-decide 记录 accept/retry/stop 和实际判断理由。accept 表示方案足以进入正式翻译，允许保留孤立错误；retry 用于验证明确的方案调整，可指定 retry_ids，省略或空列表表示重试相同整批样本；stop 暂停流程并记录原因。不能只为清零个别句子而循环返修。没有 API 独立试译审校，也没有额外 Sol 逐项验收或正式阶段的 6 轮逐句修复循环。
5. 需要调整时先结束/核实恢复在途任务，再 pause/configure/resume 修改参数、文体等；术语变更走 revise-glossary。调用 pilot-retry 指定受影响 ID（不指定则同批全部），保留其他样本、旧结果、实际作者和每轮判断。完成验证后再次 pilot-report，由主 AI 判断继续、接受或停止。不得直接改 DB、代写批次译文或改写在途输入。

主 AI 的判断保存在 TranslateFiles/reports/pilot_feedback/；各轮报告保存在 reports/pilot_rounds/。程序给出 pilot_assessment.template.json，仅需 report_hash、reviewer=main、decision 和 summary，retry_ids 可选。不要求每句决定、强制错误率阈值或逐轮修改参数。API 执行代理只负责运行和等待，不做校准判断，不自动跨轮。

```powershell
& $python $flow --project $project prepare --stage pilot_translate
# 执行本轮全部翻译批次
& $python $flow --project $project pilot-report
# 主 AI 实际阅读报告、完成整体评估后填写决定文件
& $python $flow --project $project pilot-decide --file <主AI决定JSON>
# 调整参数后，按需验证部分样本；省略 --ids 表示相同整批样本
& $python $flow --project $project pilot-retry --ids <ID...> --reason <调整原因>
```

prepare/run-api 的 pilot_review 入口已停用，执行前直接拒绝，不加载密钥或发起审校请求。quality-report --kind pilot 与 draft-scan --stage pilot_translate（即使带 --prepare-repairs）兼容转到诊断报告；approve-quality --kind pilot 使用新的主 AI 决定协议。旧在途请求先结束或核实恢复；不自动回放付费请求。旧已验收试译继续有效，历史文件不改写。未验收旧队列需要转为校准时，在确认无在途任务后明确 pilot-retry，保留历史结果。

接受后保存报告和主 AI 判断，恢复试译前的生产状态；正式正文重新翻译，沿用确认后的文体配置，并携带最多五条没有已知机械错误的试译样例供参考；主 AI 的运行诊断总结不作为文体提示词，原文与冻结术语优先。试译接受不是正式译文验收，不能跳过正式全文检查、独立语义审校、最终争议处理或实机检查。小幅配置变动保留未受影响结论；显式影响样本时恢复已存译文供校准，再由主 AI 决定是否定向重试。

## Sol 遗留争议与难题裁决

全文审校以收敛明确错误为目标。首轮独立子代理完整对照每条原译文，保留六维证据；含义准确、自然可读且符合冻结术语和既定文体即可通过。同义替换、个人偏好和另一种正确译法不列为错误或 warning，不为此返工。polish 默认关闭，不因正文完成而自动启动全文润色。

翻译实例只翻译，不兼任自审、不运行检查脚本。翻译默认按 1024 原文 Token 拆批、不限条数，独立审校最多 80 条并叠加内容、输入和输出预算；试译只复用正文翻译拆批，不执行正式审校。正文翻译先仅校验版本、ID、字段类型及锁定约束，完整保存草稿。全部草稿完成后，draft-scan --prepare-repairs 做首次全量验收并将失败 ID 跨批合并修复；修复批次仅保存候选，不逐批质量验收或插入返修；本轮所有批次完成后统一复查全部本轮修复条目，再将剩余失败项合批为下一轮，保留未变化的通过结果，不每轮重扫全文或重译通过项。问题清零后自动最终全量验收；无问题的首次全量结果可直接复用。逐轮明确区分 full 全量与 issues 局部复查，记录范围、问题、修复状态和验收结果，最终 clean 才允许准备独立审校。例外语义仍由独立审校复核，导出仍严格检查全库，不忽略遗漏。正文执行用 batch-open / batch-finish 事件交接，记录与来源见 [批次程序交接](batch_execution.md)。

独立审校改为整轮保存和验收：review/final_review 各批仍逐条语义核对并输出六维证据，batch-finish 只保存候选，不当批质量返修。全部批次完成后，运行 review-scan --stage review --prepare-repairs（润色复审用 final_review），统一严格检查证据和译文；通过项落库，失败 ID 跨原批次合并为下一轮。已通过项保留，下一轮也必须全部完成再复查。程序保存候选不代表语义验收通过，未清零不能进入 Sol 验收或导出。

Sol 最终验收按固定范围整轮收集，详细协议和样例见 [独立审校与 Sol 整轮验收](review_rounds.md)：

```powershell
& $python $flow --project $project quality-report --kind final
# 按返回 inspection_manifest 分包实际阅读，并分次保存本轮结论
& $python $flow --project $project sol-round-record --decisions <本次实际检查结论JSON>
# 本轮全部检查齐备后，才集中派修或批准
& $python $flow --project $project sol-round-finish
```

正文后 Sol 是高质量、高成本的定向难题处理者。最终报告只将独立审校明确遗留的 issues 交给 Sol 裁决；数字、否定、条件、变量、长句、术语例外和历史改动只是独立审校的提示，不自动升级；普通抽样也不交 Sol。coverage.risk_records 只是风险统计，不是派发量，实际看 coverage.sol_records 和 remaining_checks。试译改由主 AI 做整体校准评估。源文与真实审校证据保留，负责验收的 Sol 实例只裁决，不代写译文。

同源多译和角色口吻分组本身不是争议，不自动产生 Sol 检查项。独立审校发现确切冲突时须按具体 ID 留下原文依据与未解决 issues，才进入难题裁决；不得将整个组或全文转给 Sol，也不能在本轮逐条插入修订。

Sol 用 pass/contextual 记录通过，repair 写明原文依据、错误、验收要求及确切受影响语句 ID。每个本轮检查 ID 都要有结论；有任何未检查项时程序拒绝关闭或派修。检查期间 checkpoint-quality --kind final 也仅保存进度，reopen-body 不得用于逐条抢先返修。本轮范围与 hash 固定，重复 quality-report 只提供剩余待查内容；完整证据和分包归档于 reports/sol_rounds/。

本轮结束后，Sol 问题统一合批为子代理审校修复任务；全部批次完成再 review-scan。通过结论按内容签名复用，下一轮只裁决仍然遗留的具体争议；独立复查通过的修订不再触发 Sol。旧大范围轮已有的问题作为 carried_findings 保留并交修复，不重读、不静默忽略。无争议无问题时通过 sol-round-finish 完成质量闭环，不创建模型审查任务或编造逐项通过。仍有 carried_findings 时 approve/export 拒绝放行。

正文后的常规修复按条目累计最多 6 轮，之后自动生成 Sol 子代理修复任务，不询问追加轮次。任务执行重试仍使用 max_attempts；修复轮计数独立保存，不通过换任务 ID 刷新，不自动重启全量审校。试译不进入该修复链，使用 pilot-report/pilot-decide/pilot-retry；已接受试译不因正文流程升级重跑。

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

旧数据库没有 quality_version=2 时拒绝继续正文。先 status、暂停并结束/回收在途任务，再 configure 提交配置补丁；升级保留历史文件，但重置正文状态与术语冻结，重新完成带来源证据的术语审校、主 AI 试译校准及正式正文独立审校。锁定译文需先明确处理，不静默解锁。

适合入库：本参考所述配置、术语证据、已验收报告/决定、最终报告及实机验证记录与必要截图。运行数据库、任务、完整批次结果、缓存和备份仍不入库。质量门槛由自定义程序执行，AINiee GUI 路线保持原有选择。

### 防止多术语修复回退

修复包继续携带本句全部命中术语，并以 repair_constraints 区分 satisfied_terms（已有字面命中）、context_exceptions（已有语境说明）和 missing_terms（当前缺失）。仅修必要部分，不能为了一个术语破坏其他术语或将形容词误译成人物名词。字面命中只表示程序约束满足，不代表语义已经审校通过。

已有 exceptions 默认继承，返回时省略或 [] 不清除；补充或修订按 source 合并，撤销必须提供 exception_changes=[{source,action:"remove",reason:"原文依据"}]。程序不判断例外语义为真，仍保留独立审校与 Sol 验收。批次保存、直接 submit、草稿原始候选及逐条身份日志沿用同一合并规则。

仅在整轮统一复查时对比修复前后，记录 regressed_terms 和具体诊断；通过一个术语却丢失另一个也算回退。相同条目在同阶段两次回退后，自动提前进入 Sol 修复队列（required_model），不等 6 轮、不询问追加授权。标准和 Sol 条目分批派发。计数以任务去重，重复读取报告不增加次数；仍只检查本轮影响 ID，不插入当批检查，不扩大到全文。Sol 结合语境排查词义冲突，不允许用删检查或无依据例外提高通过率。

审校批量还受预计输出预算限制，Sol 升级后也有累计轮数与无进展停止条件；跨阶段/跨 revision 计数及恢复边界见 [整轮验收](review_rounds.md#修复计数与停止条件)。

任务输入和输出模板统一在派发前由程序准备；独立审校者仍负责真实语义核对，机器校验与结果合并由程序执行。详见 [统一职责](custom_workflow.md#程序准备任务子代理专注本职)。
