---
name: game-translate
description: |
  Manage the translation stage of StaticHanHua game localization. Use one shared file-driven workflow with two execution routes: Codex subagents (SubAgent) and API requests. Use for translation preparation, terminology extraction and contextual review, translation, proofreading, polishing, placeholder validation, and recovery. Do not translate a game merely because the workflow is being developed.
---

# Game Translate（翻译阶段）

## 入口与授权

修改翻译流程、执行程序或 API 交互协议时，先使用 `translation-workflow-dev` Skill；其开发原则包括尽量减少 API 模型的机械抄写、优先简单有效的优化及用数据验证。实际翻译的阶段与验收规则仍由本技能及共同程序维护。

先读项目 README.md 和已保存的 TranslateFiles/config/workflow.json。未指定路线时让用户选择 **SubAgent（Codex 子代理）** 或 **API（程序请求）**。配置保持 workflow=custom，backend=codex/api；workflow 是共用程序的历史字段，不再提供 AINiee 路线。旧 AINiee 项目需先选择现行路线并核对既有产物，不自动改写缓存或迁移进度。已有明确选择直接沿用，不重复询问。选择尚未确定时可继续源文分析，不运行正文翻译或付费请求。

- SubAgent / API：用户要求采用所选路线执行汉化即授权其内部自动处理，读取 [自定义流程及调度](references/custom_workflow.md)，调用 Tools/localization/translation_workflow.py。Codex 方式按参考规则用子代理并行任务；API 方式由程序请求用户已选服务。未指定模型时当前采用 Luna 子代理，后续用户选择优先。
- 仅术语审校：按 [术语审校规则](references/glossary_review.md)；不要因此启动全文翻译。
- 用户提供译文仅要求校验：直接执行校验，不必询问工作流或改动原项目后端。

## 共同边界

API 正文交接使用批内短编号（字符串 `1`、`2`…），模型仅返回 records 及本阶段实际字段，不抄写任务 ID、哈希或版本；程序通过 session 绑定映射恢复完整协议。占位符使用句内 `[P1]` 等短标记，原文冲突时自动换前缀。正式审校的真实证据要求保持不变。

API 术语提取仅返回 analysis，术语审校仅返回 decisions；任务及来源/快照哈希由程序绑定。术语项用 e编号，冲突用 f编号，修改和隔离用 ref，关联项只读；reviewed_refs 从完整、不重复的 entry_reviews 恢复。仍须明确返回 review_complete、真实 summary、逐项四维证据与已核对冲突清单，程序不代填语义结论。输入过滤提取任务 ID 等追踪字段，保留候选、次数、出处和实际语义理由。新增条目仍输出实际 table、完整 row 及依据。

API review/final_review/polish 可显式 keep:true 复用任务原译；修改时仅输出一次完整 translation。review/final_review 的 reviewed 标记及 changes.before/after 由程序绑定，changes 中的维度、严重度、摘录、原因和逐条六维检查仍须真实填写。缺少 translation 且没有 keep:true 必须拒收，不能把省略字段当通过；不覆盖模型明确提供的错误 before/after。原始回复和完整结果分开保存，恢复时沿用 session 映射。此适配只减少抄写，不增设模型阶段、通过缓存表或放宽共同验收。

中断后通过 `run_api_round.py --project <项目> --stage <阶段> --resume` 接续：先停止旧执行器，runner 核对旧父子进程均已结束后才回收遗留锁。成功提交批次不重派；已落盘的完整回复优先收集，未完成批次沿用原尝试预算重试。准备阶段可以重新计算，不要求保存准备进度。新协议不兼容旧版在途交接，不对旧交接直接使用新映射。

总体仍是一个翻译阶段，内部子阶段不要铺到基础 AGENTS.md 或项目概览。

自定义流程以现有 SubAgent 规范为共同基准：阶段、输入包、输出模板、批次设计、结果收集、验收及修复由同一份程序实现，统一读取 [自定义流程](references/custom_workflow.md) 和 [批次交接](references/batch_execution.md)，不维护 API 版流程副本。API 每次只发起独立请求，由程序解析结构化回复并写出同格式结果文件；不作为可自主调用工具的 agent。升级修复仍强制 Sol SubAgent。

API 输入由共用生成器使用精简指令包装，省略子代理工具/文件操作、轮询、调度及重复协议文字；保留背景、文体、本批数据、相关术语、保护规则、阶段语义要求与输出模板，审校证据和质量门槛不变。程序原样发送生成的 API input.md，既有在途交接不改写。

新API展示包的重复长元数据可通过 shared_metadata / 每条 metadata_refs 共享；资源路径用 shared_metadata_text 的 prefix_ref 与 suffix 无损拼接。字段范围和全部原值仍保留，不给缺字段记录隐式继承，canonical任务及既有交接不改。此压缩不关闭思考、不削减审校证据。无正文的推理耗尽，或以length结束且被拒收的结果，暂停交回预算/批量诊断，不原样重复消耗、放宽门槛或接受截断。

API 正文输入省略 ordinal 及空 protection/metadata/locked；同批冻结术语以 shared_terms 去重，各条 term_refs 明确实际命中范围，不凭模型重新匹配。各阶段仅附输出结构示例，完整范围由 records 的 ID 或 entries.ref 定义，结果必须全部逐条覆盖；试译由主 AI 做整体校准评估，不设置独立 API 审校或逐条六维证据。独立审校仍须逐条输出完整六维真实证据，术语审校仍须逐项输出四维真实证据。review 的 reason 简述结论，checks.evidence 分维举证，changes.reason 解释对应改动，不在三处复制全文。术语提取/审校省略调度字段及无用空字段，但保留 ordinal 供出处引用；所有候选译名、冲突、关联信息、原文、保护映射、语境例外、锁定约束和诊断不截断。持久化任务、完整结果模板、哈希、验收协议及既有在途文件保持不变。

API 正文术语例外复用本批 shared_terms 的短编号：新增例外返回 exceptions=[{term_ref:"t1",reason:"具体原句依据"}]，撤销用 exception_changes=[{exception_ref:"x1",action:"remove",reason:"具体反证"}]，x编号绑定本条 existing_exceptions，可撤销当前未命中的旧例外；新增例外的 term_ref 仅可引用本条 term_refs，不抄写术语原文。API 的 terminology_findings 同样使用 term_ref。程序在对应 session 的 api_id_transport.v2.json 中绑定逐记录的 record_terms，校验未知、跨记录、重复编号与非空字符串理由后，恢复为原有 source/reason 协议再收集。恢复和格式重试沿用这份映射，不根据回复顺序或当前全表猜测。持久化例外、语义验收及 Codex 子代理完整 source 协议不变，不新增通过缓存表或模型阶段。

API 阶段执行默认交给新的 Luna 执行代理（gpt-6-luna，fork_turns=none），由其调用 run_api_round.py 并等待进程；主代理只用原生 wait_agent 等待终结事件，不轮询程序、读运行日志或定时询问子代理。一次调用完成本阶段的普通多轮，不在内部每轮创建执行代理或重启进程；Workflow、冻结术语对象、内存命中缓存和 API 客户端持续复用。translate 仅收齐草稿后返回 drafts_complete=true、next_action=draft_check，主 AI 可检查产出或提交 Git；之后单独调用 draft_check 自动完成草稿扫描及常规修复，review/final_review 自动完成整轮扫描及失败项修复，通过项不重派；各轮沿用原共同验收和累计次数限制，不新增持久化缓存表。提取、术语审校、润色完成各自任务后交回主流程；pilot_translate 生成试译报告后等待主 AI pilot-decide，不自动接受或重试。阶段间不自动串联、不自动导出；预算暂停、异常交接、停止条件及 Sol/其他执行器任务均返回主流程。只返回一次紧凑回执，每个阶段调用用新执行代理；`--single-round` 仅供调试。执行前读取 [API 阶段执行与等待](references/api_execution_wait.md) 和 [自动循环边界](references/api_cycles.md)。不要把等待期间的周期唤醒搬到另一个 Sol，也不要承诺绝对零唤醒或零 Token。

- 正式输入：Projects/{GameName}/{gamename}_text.csv；正式输出：同目录 {gamename}_text_translated.csv。无表头、单列，按 CSV 逻辑记录逐条对应，逗号/换行合规转义；不能用物理行或模型输出顺序对齐。
- 所有任务统一由程序在分配前完成输入准备、结构/来源检查、预算拆批并提供输出模板；子代理只完成本阶段工作，不运行任务或结果校验脚本、不修包或重分批。产出后的收集、合并、机械验收仍由程序执行；语义审校不能由程序替代。详见自定义流程的“程序准备任务，子代理专注本职”。
- 所有翻译中间产物位于项目 TranslateFiles/，包括 TranslateDoc、配置、术语审校、任务、结果、报告、缓存及备份。绝不写入 Game/ 链接。既有项目不自动批量搬迁。
- 正文前完成全文术语提取、整理、程序检查和语义审校，再冻结使用；无术语亦记录检查。程序通过不等于语义审校完成。
- 自定义正文、审校和润色任务由程序筛选相关术语；子代理不读取完整表，不新增或自行修改冻结表。
- 全文翻译和润色默认按 1024 原文 Token 拆批，不限制条数；术语提取按 10000 原文 Token，不限制条数。术语审校最多 60 项并叠加 10000 候选 Token；独立审校最多 80 条并叠加 1024 内容 Token、完整输入及预计输出预算。旧字符预算停用。翻译子代理只产出译文，不运行检查脚本、不自审或反复润色。正文翻译批次仅检查版本、ID、字段类型及锁定约束并保存草稿；全部草稿完成后用 draft-scan --prepare-repairs 做首次全量验收，将失败 ID 跨原批次合批为一轮修复。修复批次同样仅保存结果，必须等本轮所有批次完成后，统一复查本轮全部修复条目，再将剩余失败 ID 重新合批为下一轮；禁止修完一批立即质量验收或插入返修。本轮未结束时程序返回 awaiting_round、检查数为 0。通过项不重派；问题清零后自动最终全量验收。逐轮记录范围、问题、任务状态与结果，不每轮扫描全文。试译只复用正文翻译的拆批与草稿保存；另按下述简洁校准流程执行。
- 自定义两种执行方式默认使用 [共用批次程序交接](references/batch_execution.md)：batch-open 一次领取并生成单文件输入包；执行器产出结果后 batch-finish 一次保存、提交并汇总。禁止短间隔 sleep/status/日志轮询，不发重复指针/协议/任务 JSON。正文、独立审校和整轮修复均不因质量问题当批返工；格式可靠性每个交接至多一次自动集中修复；试译不自动逐句返修，轮末由主 AI 判断是否需要重试。逐条保留真实产出、修订及审校身份。
- 不附带 CSV 相邻记录；提取顺序不代表剧情顺序。一致性依靠冻结术语、文体和已确认的角色/场景元数据；术语审校仍保留条目明确引用的原文出处。
- Codex 子代理默认一批一个新实例（fork_turns=none），验收后释放并创建下一批实例；三个并发槽位不对应三个长期对话。必要信息由任务文件传递，不携带上一批聊天。上下文预算与同批修复规则见自定义流程。
- 自定义 Codex 路线中，主对话只负责主流程调度、质量检查及产出物审查；Sol 是指定模型角色，主对话不是 Sol 时，用新的 Sol 子代理执行验收或升级任务，不切换主对话模型。术语提取、逐项语义审校、正文翻译、润色及译文修订由子代理执行；主代理发现问题后退回相关任务，不自行代写批次结果。具体分工见自定义流程参考。
- 自定义路线先做随机样本试译，由主 AI 判断流程、文体和总体质量适合全文后开放正式翻译；正式审校实例必须与对应产出实例不同，审校需逐项留下结构化证据。正文后 Sol 只裁决独立审校明确遗留的争议，或处理多轮未修好、反复回退而升级的定向难题；风险提示、普通抽样、已修正项和一般一致性分组不自动进入 Sol。没有遗留问题时只由程序完成最终闭环，不制造 Sol 批量复审。实机检查在回填后记录，未通过不得发布。具体协议及门槛见 [质量检查](references/quality_workflow.md)。
- 试译用于校准方案：从全文不放回随机抽取默认 150 条（不足时全部），保存 ID 并复用，不分类加权。按正文翻译的相同拆批和保护机制完成一轮，pilot-report 汇总真实译文与机械诊断；主 AI 评估链路、文体和共性错误，用 pilot-decide 记录 accept/retry/stop 及理由。需要调整时通过配置入口修订，再用 pilot-retry 指定受影响 ID（省略时重试同一批样本）。不要求样本逐句零错误，不安排 API 独立审校、逐条六维证据或额外 Sol 验收，也不套用正式阶段的 6 轮逐句修复循环。保留每轮结果和判断，已验收旧试译不自动重跑；正式翻译仍重新执行完整翻译及独立审校。详见 [试译校准](references/quality_workflow.md#随机样本试译)。
- 占位符、字面转义、标签与变量严格保护；源文中的玩家选项不误当机器变量。细则见 [保护边界](references/placeholder_patterns.md)。
- 两条路线使用共用 ID JSON 协议，按对应执行器生成输入和结果模板。
- 输入 Token 及预计输出预算统一放在任务分配阶段，完整组成输入后自动拆批，必要的关联术语及出处不截断；只有单项仍超限才报错。领取阶段直接使用分配结果，不重复预算校验。旧版未尝试待办任务在 prepare 时自动重新分配。
- AI 可在执行中检查和调整；通过程序暂停、修订和增量复查，不能直接改 DB、覆盖在途版本或忽略锁定译文。按配置限制重试，关键未解决问题不能标记通过。
- 术语变化只复查命中新旧术语差异的语句；提示词、文体和背景的微调默认不扩大范围。确实影响部分已有译文时显式指定 ID，改变全文要求时才全量处理；保留无关译文、审校证据和通过结论，影响判断写入变更原因。
- 全文首轮完整独立审校一次，各批只保存候选及六维证据，不提前算语义通过；所有批次完成后 review-scan 统一验收，通过项落库，失败项跨批合并为下一轮。Sol 最终验收先固定本轮范围、分包检查并记录结论，全部检查完成后 sol-round-finish 才集中派修或批准；禁止发现一条就插入修复。独立审校后续只处理受影响条目，Sol 只裁决仍然遗留的具体争议；保留通过结论、身份与尝试预算。具体入口见 [审校与 Sol 整轮验收](references/review_rounds.md)。含义准确、自然可读就通过，不因同义偏好返工，不自动全文润色或重启全量审校；正文后常规修复最多 6 轮，仍失败自动交新的 Sol 子代理定向修复，不询问追加轮次；见整轮验收规则。
- 正式译文多术语修复必须保留 repair_constraints 中已满足的术语和已有语境例外，只作必要修改。exceptions 省略或空列表均继承已有例外；撤销用 exception_changes 明确术语、remove 动作及原文理由。整轮复查记录 regressed_terms；同一条目在同阶段两次将已满足术语改坏，自动提前派新 Sol 子代理，不等满 6 轮。通过项不重派，例外仍须语义审校，不修改冻结表。
- 程序术语疑问允许模型作“现译正确”的有理由结论：输入 terminology_findings 列出本句缺少字面译名的 source/expected_translation。模型确有错误则修正；坚持现译正确则保留译文，逐项通过 exceptions=[{source,reason}] 说明原句依据及不套用译名的原因。程序按现有结构、关联性及机械规则验收后保存为本句术语通过记录；后续扫描、修复、审校和导出沿用，不因缺少同一字面译名反复返修。独立审校仍核对全文含义；确有新依据推翻时才显式撤销该例外。空理由、仅重复原译文或只写顶层 reason 不关闭术语疑问，不豁免标签/占位符等错误，不形成全局术语豁免。
- 正式译文质量修复轮数按 ID 跨阶段、跨 revision 累计；Sol 默认最多 3 轮，同一诊断连续两轮未解决则停止该条自动派发并报告 needs_attention，不重置历史、不放宽导出门槛。审校输出预算与具体停止规则见整轮验收参考。
- 缓存/备份/运行状态及凭据不得提交。Game/ 永不提交。

## 验证与交接

两条路线均必须由程序 export 完成其全部质量门槛，不能手工拼接最终 CSV 绕过验收。用户仅要求校验已有译文时，可使用 scripts/validate_translation.py（--src-col 0 --dst-col 0，报告和备份指向 TranslateFiles）；独立校验不能替代正式流程验收。

更新 README 的翻译节与技术栈，记录方式/后端/模型、术语版本和审校范围、占位符、验证及待确认问题；按项目规范提交 README、正式译文和 TranslateFiles 下适合入库的配置/审校/交付材料。仅准备阶段用“文档”提交，翻译通过用“翻译”提交。
