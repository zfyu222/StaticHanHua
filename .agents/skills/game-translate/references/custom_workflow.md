# 共用翻译工作流（SubAgent / API）

读取本参考后，由主代理调用程序管理任务；仅提供 SubAgent（Codex 子代理）和 API 两条路线。配置保留 workflow=custom 历史字段，通过 backend=codex/api 选择执行器；AINiee 目录仅是现有底层复用依赖。术语整理必须先完成，正文任务不承担术语发现职责。

## 共用流程与可替换执行器

本文件维护唯一的阶段流程；[批次交接](batch_execution.md) 维护统一输入产出和收集、修复规则，均以既有 SubAgent 流程为准。API 和 SubAgent 共用 `translation_workflow.py` 及其批次、分配和质量模块；禁止复制为两份流程文档或程序。修改阶段、术语规则、批次上限、提示词、输出模板或验收逻辑时，修改共用实现，两种执行方式同时生效。

所有阶段均通过 `batch-open` 生成同一份 `input.md` 和输出模板，产出同格式 `result.json` 后由 `batch-finish` 收集。术语任务仍保留 analysis/decisions 协议，正文任务使用 records 协议；差别由阶段决定，与执行器无关。旧 claim/prompt/submit 入口只作兼容。

SubAgent 读取输入包并写结果文件；API 适配器将该输入包原文作为一次请求发送，解析单个 JSON 回复，由程序保存结果文件，再调用同一收集入口。API 请求没有工具调用、会话历史或自主 agent 循环。Codex 路线仍由主流程逐轮调度；API 的 translate 收齐正式草稿后先交回主 AI；单独调用 draft_check 后，draft_check/review/final_review 各自在同一进程内自动调用共同整轮检查和常规失败项重派，复用内存缓存，见 [自动循环边界](api_cycles.md)。阶段切换、试译整体评估、术语冻结及导出仍由主流程按规则推进。升级修复保留强制使用 Sol SubAgent，正式争议裁决按质量协议执行。

任务拆分统一计算同一输入包预算和 `review_output_tokens`，不按执行器另拆一套批次。API 的 max_completion_tokens 必须满足相同审校输出预算，不足时派发前报错，调整服务额度后继续；不自动降低共同标准。更换执行方式通过 pause/configure/resume，保留已完成产出、审校证据与累计预算；在途任务先结束或核实恢复。

## 程序与初始化

```powershell
$python = 'D:\StaticHanHua\Tools\AINiee\venv\Scripts\python.exe'
$flow = 'D:\StaticHanHua\Tools\localization\translation_workflow.py'
$project = 'D:\StaticHanHua\Projects\GameName'
& $python $flow --project $project init --source "$project\gamename_text.csv" --config <项目配置文件>
& $python $flow --project $project status
```

项目配置为 JSON，只写需要覆盖的默认项。可用 init --context 提供提取上下文 JSON（按逻辑记录号索引，含 group、speaker、resource、scene、text_type、note）；正文翻译、审校和润色跨 group 合批，保留每条元数据，不把批内记录当作连续剧情。术语提取仍按 group 分批，试译按正文相同方法拆批。源文、正式译文在项目根目录；所有其他翻译文件放 TranslateFiles，包括输入配置。不要在 Game/ 链接内运行。

常用配置：workflow=custom，backend=codex/api，model=gpt-6-luna，source_language、target_language、style、batch_records、batch_tokens、task_input_tokens、context_records、case_sensitive、whole_word、protect_named_curly、exclusions、polish、max_attempts。`protect_named_curly=null` 沿用现有游戏选项识别；结合原文确认命名花括号是否是变量，必要时显式覆盖。Token 计数复用 AINiee 的 o200k_base（加载失败时沿用其降级估算）；不是 MiMo 专属 tokenizer。batch_chars/task_input_chars 仅兼容旧配置，不再参与拆批。

exclusions 使用 AINiee 禁翻表条目结构：markers、regex、info（可空）；markers/regex 至少一项非空。stage_backends 可为各阶段指定 codex/api，未指定则沿用 backend。设置风格时读取项目 README 和提取上下文；不能杜撰角色或场景。

context_records 默认 0，仅作为旧配置兼容字段保留，不再按 CSV 邻近位置附带记录，即使旧配置为 2 也不启用。提取顺序不保证剧情延续性；前后一致性通过冻结术语表、文体、试译参考和已确认的元数据维持。术语审校任务保留条目明确引用的原文出处，不能误删这些审校证据。旧版本已生成且含相邻文本的任务需暂停、结束/回收在途任务，再 configure 设置 context_records=0 并重新 prepare；程序拒绝继续领取这类旧任务，保留已验收结果。

`game_background=null`（默认）在 init 时自动读取 README 的游戏信息、简介、背景/世界观、人物设定、汉化目标及文体章节，保存 `config/game_background.json` 快照；技术步骤和进度日志不进入背景。每阶段的 Luna/API 任务自动携带相同背景和配置文体。主代理应核对背景完整性，根据已核实资料自动整理游戏文体；需要压缩或更正时用 game_background 字符串及 style 覆盖，不能编造未记载的设定。缺少背景章节时明确记录缺失，按原文处理。README 后续更新不会暗中改变在途提示词；暂停并通过 configure 提交 `{"game_background":null}` 重新读取，或提交修订摘要。文体和背景提示的小改动默认保留完成状态与验收；确实影响部分已有译文时指定 --impact ids --ids <ID...>，改变全文要求时显式 --impact all。影响译名时按术语命中范围复查。

默认整批 API 请求/结果不会写入 AINiee 的全局 config 或原 GUI 缓存。程序依赖当前本地 AINiee 修复版及技能内占位符脚本；每个运行固定其模块 hash，变更后拒绝继续，先回归再创建新运行。

## 固定阶段及质量门槛

按顺序 `prepare --stage <阶段>`；将该阶段所有任务执行并验收完成后才进入下一个阶段。

执行自定义路线前同时读取 [质量规范](quality_workflow.md)。默认试译随机抽取 150 条并保存 ID，不足时全部。试译只复用正文的翻译拆批和执行机制；程序报告问题后，由主 AI 校准方案，不复制正式的独立审校和逐句修复链。pilot_batch_count/pilot_call_limit 为停用兼容字段。

1. `extract`：覆盖全部源文，收集候选，条目保留 evidence_rows（从 1 开始的 CSV 逻辑记录号），不能凭提示词示例生成专名。结果含 analysis={characters,terms,non_translate}，采用 AINiee analysis_v1 字段。
2. `glossary_review`：程序汇总同一条目、运行共享 glossary_tool 的检查、按条目分批提供出处。任务只有分配条目，table:index 指向完整原快照。每项做语义核对，提交 reviewed_refs、review_summary、所有命中自动 finding 的处理与带依据的决定。跨批冲突由主代理汇总处理；不能把程序检查通过等同审校完成。
   成本控制：`glossary_families.py` 只在本地对已提取术语生成候选关系（英文冠词、所有格、部分单复数、保守派生词根和完整词组件），保存 `glossary/r{revision}/family_candidates.json`。尽量同族同批，超大词族按批次上限拆开；不增加模型阶段、全表复审或全文变体补提取。每批 term_families 最多 120 条候选关系及 12 个跨批精简关联词，不附加这些词的全文出处。候选不是语义等同结论，不自动改译名；在既有 entry_reviews.consistency 及修订理由中核对核心译名，保留合理差异，不额外生成词族报告。原有自动 finding 的必要关联项仍按旧机制提供，不受该新增提示上限裁剪。完整任务仍受 task_input_tokens 限制。规则不是通用词形分析器，不保证识别所有语言、派生词或别名；已生成任务不自动重排，冻结表不变。
3. `freeze-glossary`：汇总审校决定并调用共享 build；全部检查通过才冻结正式表。无术语仍要确认全文检查为空。失败时用 `reopen-review --task <相关审校任务> --reason <原因>` 重新领取、修正、提交，再冻结；已提交任务不能直接覆盖，不编辑 DB。轮次仍受 max_attempts 限制。
4. `pilot_translate` → `pilot-report` → 主 AI `pilot-decide`：固定随机样本完成一轮翻译，报告真实译文、机械诊断和任务状态；主 AI 判断链路、文体及共性错误，用 accept/retry/stop 记录整体结论。按需调整基础方案并 pilot-retry 同批或指定 ID，保留每轮结果。不设独立 API 试译审校、逐条六维证据、额外 Sol 验收或自动逐句清零要求；接受后开放全文，详见质量规范。
5. `translate`：试译验收通过后，程序按 ID 分批，筛选相关术语、人物信息、保留原文要求。翻译实例只翻译并写出 records=[{id,translation}]，普通词义可附 exceptions，不运行检查脚本、不生成审校报告、不自审或反复润色；完成后结束。每个任务附术语版本与 input_hash。批次只检查版本、ID、字段类型与锁定约束并保存草稿，不因术语、占位符或空译文当批返工。原始候选及保护映射留存，供全文程序检查使用。
6. 全文草稿完成、在途任务结束后执行 `draft-scan --prepare-repairs`：首次全量验收占位符、结构、空译文、术语及例外；仅将失败 ID 跨原批次合并为同一轮 translate 修复任务，每批沿用正文 Token 预算（默认不限制条数）。通过 batch-open --stage translate 领取修复任务，输入仅含失败句、当前译文、诊断和相关术语，按 ID 合并。修复批次只保存候选，不立即质量验收或返修；本轮全部批次完成后才调用 draft-scan，统一复查本轮全部修复条目及其他变化条目，未变化的通过项不再检查或翻译，仍失败项继续修复；问题清零后自动最终全量验收，不能每轮重扫全文。首次全量无问题时直接复用该结果。最新汇总为 reports/full_draft_scan.json，逐轮范围、问题、修复状态与结果见 reports/draft_checks/，具体分流见 [批次程序交接](batch_execution.md)。旧工具有效的全量报告可核对版本后复用，没有有效检查记录时才首次全量，无需重置译文。clean 后才 `prepare --stage review`，由不同实例全量逐条对照原文、译文、术语及上下文，提交六维 checks、source_excerpt、修改前后及错误类型。疑难项写 issues；语境例外绑定命中术语和理由。程序通过不代表语义审校完成。review/final_review 的批次只保存候选与证据，整轮全部完成后执行 review-scan --stage <阶段> --prepare-repairs，再将失败 ID 跨批合成下一轮，通过项不重审。
7. 可选 `polish` → `final_review`：润色保留含义，改变时记录理由；复审实例必须与对应译文和润色产出实例不同。未启用 polish 时无需这两步。
8. Sol 难题裁决：quality-report --kind final 只交付独立审校明确遗留的 issues，不把风险标签、普通抽样、已修正条目、同源多译或角色口吻组自动交给 Sol。Sol 按 inspection_manifest 裁决争议，用 sol-round-record 保存 pass/contextual/repair；全部本轮争议结束后 sol-round-finish 集中派修或关闭。没有遗留争议时无需模型逐项检查，直接完成程序闭环；已有 Sol 发现的问题通过 carried_findings 保留并定向修复，不重读、不自动批准。普通修复多轮失败或反复回退才升级 Sol 子代理；修复复查通过后不因曾经改动重派 Sol。试译另由主 AI 整体评估。完整协议见 [审校与 Sol 整轮验收](review_rounds.md)。
9. `export`：程序检查全部记录及当前 Sol 验收，才导出根目录单列 CSV 和 final_validation.json；实机状态记为 pending_repack。交接回填后按质量参考完成字体、排版、UI、对白及任务检查，record-game-check 保存版本、载荷与证据；release-check 通过后才发布。

任务协议、完整字段及提示词由程序 `prompt` 命令生成。不要使用 AINiee 的 textarea 输出协议处理自定义任务，也不要根据回复顺序推测 ID。

## 共用任务执行与 SubAgent 调用方式

### 批次与上下文生命周期

默认不是三个实例各翻译三分之一全文，而是同时最多三个批次，每个批次使用一个新实例（fork_turns=none）。验收成功后该实例结束，下一批重新 spawn_agent；不能 followup_task 给完成的实例继续下一批。状态、译文、术语与审校证据由项目文件和数据库保存，聊天历史不作为进度或知识库。

- agent_task_limit=1：同一实际实例最多领取一个不同任务；程序会拒绝超过工作量的领取。更改 worker 别名不能代替新上下文。
- agent_attempt_limit=2：同一实例最多领取两次（包括同批恢复/重试），不是允许处理第二个不同任务。同一租约内至多一次局部格式或遗漏修复；需要再次修复时记录 fail --retry 并交新实例，仍受任务总 max_attempts 限制。
- 默认正文翻译/润色按原文 batch_tokens=1024 拆批，batch_records=null，不限制条数；术语提取按原文 extract_batch_tokens=10000，extract_batch_records=null。这两项 Token 值对齐当前 AINiee。术语语义审校保留 glossary_review_batch_records=60，同时限制候选条目 glossary_review_batch_tokens=10000；独立审校/复审保留 review_batch_records=80，同时按原文、译文和问题描述的 review_batch_tokens=1024 及预计输出预算拆批。试译翻译复用正文相同拆批方法与预算，不执行独立试译审校，无试译专用条数或累计执行次数上限，pilot-plan 提供样本和预计任务信息。仅原文内容超过 Token 预算的单条完整保留、单独成批；完整输入和审校输出超限仍报错，不截断原文或证据。
- task_input_tokens=16384：完整输入包的额外 Token 安全预算，包含背景、术语、关联条目、出处、试译参考、保护映射和输出模板；此预算是自定义流程的额外保护，不是 AINiee 的原文批量值。分配阶段按共用保守包装计数并自动拆批，API 与 SubAgent 保持相同任务范围。必要出处不截断，单项完整输入仍超限时报错并回滚。领取不重算预算。旧版未尝试待办任务在 prepare 时升级到 Token 合约 v3；已完成、已尝试和在途任务保留，不把历史字符统计冒充 Token 数。
- 每个新实例只读取自己的任务与提示词，获得相同游戏背景、文体、冻结相关术语、已确认的角色/场景元数据、本批原文及待审译文和通过的试译样例。不附带 CSV 相邻记录，不加载已完成任务的完整聊天、全库或日志；不据提取顺序推断剧情关系。
- 主流程通过 batch-open 领取，只接收任务 ID、文件路径、租约和预算信息；完整任务包留在文件中。SubAgent 结束时仅报告任务 ID、结果文件路径、完成状态及简短疑难项，API 由程序接收 JSON 并写入同一结果路径；完整结果不反复进入调度对话。

默认每批换上下文适用于所有 Codex 内部阶段。仅当用户明确希望同一实例连续处理紧密相关的小批次时才提高 agent_task_limit，同时检查 agent_attempt_limit 和完整输入预算；独立审校约束始终有效。API 请求本身已逐次使用独立上下文。调整以上调度字段使用 pause/configure/resume，不重置已完成译文。

既有运行沿用新上下文生命周期的默认限制，但已经生成的大任务不会自动改 ID 或拆分。需要重新分批时先暂停、结束/回收在途任务，再 configure 设置批次与输入预算并重新 prepare；保留已验收结果，不直接编辑任务文件或数据库。

术语语义审校默认每批最多 60 个条目，同时受候选条目 10000 Token、完整输入和必要出处预算限制。显式设置非空 batch_records 时，该兼容条数限制仍叠加生效；默认及统一本地配置设为 null。调整通过 pause/configure/resume 或 api.local.jsonc 的 batching 同步，不改写在途输入，不重跑已完成审校。

既有项目显式条数配置保留，若要采用本次对齐规则，设置 batch_records=null、extract_batch_records=null 及对应 Token 预算。API 路线从已更新的 api.local.jsonc 自动同步，原已完成译文和审校结论不重跑；旧字符字段已停用，不做隐式字符到 Token 的固定比例换算。

批次由用户所选的执行器完成，主对话负责主流程和产出物审查，不承担术语提取或正文翻译。选择 SubAgent 时，按当前可用并发容量派发，按项目 model 配置运行（默认 Luna `gpt-6-luna`，`fork_turns=none`）；API 使用已选服务和模型逐次请求，同样独立完成各批本职工作。Sol 指验收/升级任务使用的模型角色；既有验收职责沿用质量协议，升级修复强制 Sol SubAgent。

- 子代理负责 `extract`、`glossary_review`、`pilot_translate`、`translate`、`review`、可选 `polish` 和 `final_review` 的批次结果，以及退回后的术语与译文修订。全量逐条语义审校仍由子代理完成，主代理审查不替代该质量门槛。
- 主代理负责准备配置和上下文、领取及派发任务、租约和重试管理、调用程序验收、审查术语依据与跨批一致性、审查译文及报告、冻结术语、导出与文档交接。发现错误或冲突时写明问题和验收要求，退回相关子代理任务；不自行生成、改写或补齐批次术语和译文结果。
- 主代理审查跨批冲突并确定复核范围，具体语境核对和修订结果由子代理按任务协议提交；依然通过程序接受结果，不直接改数据库或冻结表。
- 子代理并发数按运行环境剩余槽位安排，主代理即使只做调度和审查也占一个槽位。当前会话总容量为 4 时最多并行 3 个子代理；阶段按顺序推进，批次结束后继续领取下一批，不把该数值写死为其他环境的限制。

正文、审校和润色子代理只读取任务内筛选的相关术语；术语提取与术语审校子代理读取各自分配的源文或条目。

所有阶段的交接、结果路径、可靠性修复与整轮复查，统一执行 [共用批次规范](batch_execution.md)，不在此维护另一套操作步骤。SubAgent 先创建等待任务的新实例，取得真实 ID 后交给 batch-open，再发送返回的 input_file/result_file；API 适配器在程序内调用同一入口。提取与术语审校、翻译与审校、润色与复审分别使用不同执行身份。旧 claim/prompt/submit 仅兼容历史任务，不能混用旧 single_task.py 的 check 循环。

领取租约即将到期时 `heartbeat --task ... --token ...`。进程重启后查看 status；确认旧 worker 已停止才 `recover --task ... --worker-stopped`。租约过期不表示 worker 一定停止，不能盲目重发。

## API 执行

这里只规定传输配置；全部阶段、批次、结果保存和修复规则沿用上方共同流程及 [批次交接](batch_execution.md)，不另定义 API 工作流。

API 的 input.md 使用共用生成器的精简指令：保留语言、文体、背景、本批原文与元数据、相关术语、保护规则、阶段语义要求和输出模板；去掉本地文件读写、工具使用、轮询、调度与整轮验收过程说明及重复协议文字。API 仍逐字发送自己生成的 input.md，不在发送时临时改写。任务数据、ID/hash、结果格式、拆批与质量门槛和 SubAgent 共用；输入分配沿用共同保守预算，不因指令变短自动扩大 API 批次。审校的逐条语义证据不得压缩为笼统通过。既有在途交接不重写，后续新交接使用精简指令。

API 展示允许省去程序已知字段，由 session 映射恢复上述共同结果协议：extract 只返回 analysis；glossary_review 只返回 decisions，用 e编号引用条目、f编号确认实际核对的冲突，无需抄写任务/来源/快照哈希或 reviewed_refs。程序只从完整 entry_reviews 推导覆盖清单，不代填四维证据、完成声明或冲突确认。review/final_review/polish 未改译文用 keep:true，改动只输出一次完整 translation；审校 changes 的 before/after 和 reviewed 标记由程序绑定，六维证据及实际修改依据仍由模型提供。未知/跨任务引用、隐式省略译文或冲突字段仍拒收，原始回复与完整结果均保存，接续不重调已完成请求；不新增阶段或缓存表。

统一本地配置的 batching 对象可填写 batch_records、review_batch_records、extract_batch_records、glossary_review_batch_records、batch_tokens、extract_batch_tokens、glossary_review_batch_tokens、review_batch_tokens、task_input_tokens、review_output_tokens、review_output_reserve_percent，以及 review_estimate（full_base_tokens、ascii_chars_per_token、non_ascii_tokens_per_char、translation_multiplier）。默认值及字段说明见 Tools/localization/api.example.jsonc 和 README.md。run-api 启动时先通过既有 configure 机制同步这些非敏感调度字段，再准备任务；已完成译文及质量结论保留，无变更不创建新修订。有在途任务时拒绝应用预算变化，不重写输入包；先完成/核实恢复旧任务。密钥不得进入配置修订历史。预估公式仅作拆批，不放宽证据协议。

第一版支持 OpenAI 兼容 Chat Completions 接口（例如 DeepSeek 或自定义兼容服务）；原 AINiee 工作流仍保留其他原生接口。自定义 API 的地址、模型和密钥统一保存在工作区 `Tools/localization/api.local.jsonc`；可在 `apis` 下定义多个命名 API，用 `default_api` 指定默认项，或用 `run-api --api <名称>`（`run_api_round.py --api <名称>`）在单次运行中切换；也可沿用顶层平铺 api_url、model_name、api_key 的旧写法。可参考同目录 api.example.jsonc。该文件排除 Git，所有项目共用，run-api 每次启动读取一次；不要读取、打印或迁移 AINiee Resource/config.json 的密钥。不得把本地密钥写进任务、项目配置、数据库或历史。用户只需在本地文件填写，不在聊天中粘贴密钥。文件不存在或三项连接信息全空时兼容原项目配置与 api_key_env；选中条目缺字段或名称未定义时报错并列出可用名称，不混用另一来源，不静默改用其他 API。本地还可填写其他 API 运行参数，覆盖项目同名字段。修改在下次启动生效，先结束旧运行器，不重写在途交接。

api 配置：api_url、model_name、api_key_env、target_platform、think_switch、temperature、request_timeout、max_completion_tokens、max_requests、requests_per_minute、concurrency。并发、连接工厂、服务思考参数与 SSE 解析复用本地 AINiee 改进版，HTTP/2 保持本地默认关闭。max_requests 为本次运行累计请求上限，SDK 自动重试关闭，程序分类、有上限地重试。

API 最大并发默认 50，表示最多同时执行 50 个独立批次请求；不受 SubAgent 槽位数限制，也不改变批次条数、阶段顺序或整轮验收。推荐在统一的 api.local.jsonc 填写 concurrency；未填写时沿用项目 api.concurrency。默认值仅在程序 DEFAULT 中定义；旧项目仍可通过 pause/configure/resume 修改项目设置，不直接编辑运行快照。修改在下一次 run-api 启动时生效。在途请求先结束，避免两个运行器重叠。

`api.requests_per_minute` 独立控制请求启动速率，默认 0 表示不额外限速，仅按并发上限调度；正整数表示每分钟请求上限，可能让实际并发低于 concurrency。已有项目保存的速率限制保留，可在服务允许时显式设为 0；服务端 429 仍按既有有界重试处理。run-api 返回 concurrency_limit 和 peak_in_flight，分别记录配置上限与实际同时进入请求调用的峰值；待办批次不足时无需占满并发槽。累计 max_requests 请求预算继续生效，并发不刷新预算。

默认 max_completion_tokens=16384，与共同 review_output_tokens 一致；已有项目显式旧值保留，不足时报错。请求预算耗尽时保留已收集条目及待修复交接；没有在途请求且已 pause 时，可通过 configure 只调整 API 的请求/输出额度、并发、限流或超时，不切换 revision、不重派通过项、不刷新任务尝试与逐条修复计数，再 resume/run-api 继续待派修复。改阶段、后端或语义规则仍先结束/恢复在途任务。

```powershell
& $python $flow --project $project run-api --stage translate
```

服务端错误、429 和连接错误延迟重试；认证或非重试错误暂停并报告。达到请求预算暂停，已取得译文保留。用户未选择 API 时不能自动切换过去；需要 API 凭据时让用户在 api.local.jsonc 本地填写，不让用户在聊天粘贴密钥。

run-api 一次执行当前阶段的常规循环，按并发上限持续补位，自动收集、落盘并处理允许的传输/格式重试和可靠性修复。translate 仅完成正式草稿后交回主 AI，可在此检查产出或提交 Git；单独调用 draft_check 后，draft_check/review/final_review 各自在同一进程内完成共同整轮检查及普通失败项下一轮，通过项不重派，内存术语缓存和客户端复用；不需要主代理逐批或逐轮再次调用。适用于术语提取、术语审校、试译、正文翻译、独立审校、润色及其 API 修复轮；各阶段边界见 [自动循环边界](api_cycles.md)。主代理把本阶段交给新的 Luna 执行代理调用 run_api_round.py，并进入原生 wait_agent 等待终结事件，不查询 exec session、status、日志或按批次唤醒。Luna 只管理进程等待并返回紧凑 receipt，不执行语义任务或自动跨阶段。具体命令、等待与恢复边界见 [API 阶段执行与等待](api_execution_wait.md)。

程序退出后主流程只读取一次阶段汇总：translate 返回后由主 AI 决定何时单独启动 draft_check；draft_check/review/final_review 内的普通扫描和后续修复已经自动推进，查看 stage_clean、next_action 和 api_rounds 决定下一阶段或处理升级。试译已生成 pilot-report，由主 AI 校准并 pilot-decide；术语审校后按既有规则 freeze-glossary。内部各轮仍严格收齐后才统一检查，不跳过试译决定、Sol 升级或其他门槛，不自动串联阶段及导出。预算耗尽、鉴权/执行故障、needs_attention 或 Sol 任务均交回；须核对停止原因，不能把正常交回等同于全文质量通过。max_requests 是跨阶段累计预算，不因内部下一轮或重启刷新。单轮调试使用 --single-round；正常执行不使用此开关。

## 调整、修复与恢复

```powershell
& $python $flow --project $project pause
& $python $flow --project $project configure --file <配置补丁JSON> --reason <原因>
& $python $flow --project $project resume
```

修改配置前必须暂停并结束/回收全部在途任务。文体/背景提示的微调默认只影响后续任务，不重置旧译文或验收；reason 说明为何不影响已完成内容。需修正部分已有译文时用 --impact ids --ids <ID...>，只将这些语句退回审校；实质改变全文要求时显式 --impact all。源/目标语言、匹配和保护规则等影响数据解释的变更仍全量重置，不能用 --impact none 跳过。只改调度/后端/预算保留已完成记录。影响范围和验收沿用来源写入历史；任务冻结 input_hash、版本及租约，旧版本提交会被拒绝。不要直接编辑 workflow.json 或 DB 修改配置。

术语修订仍先完成全表语义审校，生成带 source/snapshot hash 的 glossary_tool 决定文件，并列出全部 reviewed_refs；暂停且无在途任务时用 `revise-glossary --snapshot <原表> --decisions <决定> --reason <原因>`。程序 build、冻结新表，比较每条记录原文和 speaker 命中的旧/新术语，只将受影响的已译语句退回审校，保留现有译文用于对照；未译语句按新表正常翻译。未命中变化的语句、审校证据和 Sol 通过结论保留。已接受试译若含受影响语句，恢复已保存的样本译文供主 AI 重新评估，并按需 pilot-retry 命中变化的 ID，其他样本保留；不命中变化则沿用试译验收。正文子代理不能自行新增术语。准备遗漏或筛选错误应当作前置阶段/工具缺陷修复。

`lock/unlock --record <ID> --reason <原因>` 保护已确认译文；解锁有记录，影响锁定译文的语义调整会被拒绝。正文草稿中的 blocking 随原始结果留存并在全文扫描时拦截；严格阶段有 unresolved blocking 问题时不能验收为完成。

适合提交：配置、TranslateDoc、原术语表、审校决定、审校版、检查/修改清单、manifest、最终验证报告和必要变更记录。state、tasks、results、source 快照、cache、backups、logs 默认忽略；不提交凭据和完整缓存。

### 正文后修复预算

正文后修复轮次采用 body_repair_round_limit（默认且最多 6），与 max_attempts（单任务执行重试）分开。超过常规轮次自动派发 Sol 子代理，按任务 required_model 执行，不要求用户追加授权；详见 [整轮验收](review_rounds.md#正文后自动升级)。试译不套用正文逐句修复轮次，只保留相同任务执行重试及 API 额度；术语规则不变。

审校批量还受预计输出预算限制，Sol 升级后也有累计轮数与无进展停止条件；跨阶段/跨 revision 计数及恢复边界见 [整轮验收](review_rounds.md#修复计数与停止条件)。


## 程序准备任务，子代理专注本职

正式执行阶段统一遵循：程序准备合格输入 → 子代理完成本阶段工作并写出结果 → 程序收集、合并、验收 → 程序生成下一轮任务。试译只复用任务准备与结果保存，由主 AI 整体判断是否需要下一轮。适用于术语提取、术语审校、试译、正文翻译、独立审校、润色、复审、所有定向修复、Sol 裁决与 API 路线。

术语审校分配前先按英文原词合并（忽略大小写和首尾空白）：多个译名也只算一条，汇总出处并保留 extraction_candidates。暂定译名按提取提案次数最多者选择，同票取首次出现者，最终由审校者确认。同一原词只分配一次，输入超限不把候选拆给不同实例；已有旧审校快照不自动重写。详见术语审校参考。

程序在分配前负责：来源与修订绑定、ID/逻辑记录号一致性、原文完整性和保护映射、字段类型、已有译文/锁定约束、继承例外与修复计数、相关术语和必要出处筛选、完整输入及预计输出预算、自动拆批。坏输入在分配阶段报错，不交给子代理修包。新分配记录保存 input_contract_version=2；prepare 自动更新未尝试的旧待办任务，已完成和在途任务保留。

程序提供 output_template / result.template.json，预填任务 ID、版本、记录 ID、术语 ref 和必要字段骨架。语义状态仍是 false/null/pending，证据和理由留空；不得由程序自动填 pass、编造证据或代替语义审校。子代理只填本阶段的实际译文、术语决定、审校证据或疑难裁决。

所有阶段由 batch-open 返回包含任务材料及模板的单文件 input_file 和独立 result_file，主流程派发这两个路径即可；API 程序读取同一 input_file。prompt_file/task_file 仅保留兼容，不再重复发送。task_ready=true 表示本次交接已由程序准备；false/旧合约应由主流程处理，不让执行模型补做校验。

子代理不执行任务预算、版本、ID 集合、字段结构、占位符统计或 JSON 合规检查，不运行 validate/check/audit 脚本，不重分批、不扫描全库、不修任务文件/数据库。翻译实例只翻译；审校实例逐条做语义核对并填写证据；润色实例只作必要润色；Sol 实例只裁决或修复分配的难题。语义核对属于审校本职，不能因机械检查前置而省略。

发现文件缺失、字段损坏或源文不对应时，只简短报告 input_problem，由主流程定位输入生成问题。原文本身的歧义按本阶段 issues/语境例外处理。程序负责产出后的解析、ID 对齐、占位符/结构/术语校验、结果合并和整轮验收；实际执行次数及真实产出身份仍完整记账。租约、实际 worker 独立性和执行次数等运行状态由程序管理，不能要求子代理自行证明。
