# 自定义翻译工作流

读取本参考后，由主代理调用程序管理任务；执行选择可为 Codex 子代理或 API。术语整理必须先完成，正文任务不承担术语发现职责。

## 程序与初始化

```powershell
$python = 'D:\StaticHanHua\Tools\AINiee\venv\Scripts\python.exe'
$flow = 'D:\StaticHanHua\Tools\localization\translation_workflow.py'
$project = 'D:\StaticHanHua\Projects\GameName'
& $python $flow --project $project init --source "$project\gamename_text.csv" --config <项目配置文件>
& $python $flow --project $project status
```

项目配置为 JSON，只写需要覆盖的默认项。可用 init --context 提供提取上下文 JSON（按逻辑记录号索引，含 group、speaker、resource、scene、text_type、note）；正文按 group 分批，试译跨 group 合批且保留每条元数据。源文、正式译文在项目根目录；所有其他翻译文件放 TranslateFiles，包括输入配置。不要在 Game/ 链接内运行。

常用配置：workflow=custom，backend=codex/api，model=gpt-6-luna，source_language、target_language、style、batch_records、batch_chars、context_records、case_sensitive、whole_word、protect_named_curly、exclusions、polish、max_attempts。`protect_named_curly=null` 沿用现有游戏选项识别；结合原文确认命名花括号是否是变量，必要时显式覆盖。`batch_chars` 是字符预算，不是精确 token 数；长单条记录需单独处理并核对模型上下文容量。

exclusions 使用 AINiee 禁翻表条目结构：markers、regex、info（可空）；markers/regex 至少一项非空。stage_backends 可为各阶段指定 codex/api，未指定则沿用 backend。设置风格时读取项目 README 和提取上下文；不能杜撰角色或场景。

context_records 默认 0，仅作为旧配置兼容字段保留，不再按 CSV 邻近位置附带记录，即使旧配置为 2 也不启用。提取顺序不保证剧情延续性；前后一致性通过冻结术语表、文体、试译参考和已确认的元数据维持。术语审校任务保留条目明确引用的原文出处，不能误删这些审校证据。旧版本已生成且含相邻文本的任务需暂停、结束/回收在途任务，再 configure 设置 context_records=0 并重新 prepare；程序拒绝继续领取这类旧任务，保留已验收结果。

`game_background=null`（默认）在 init 时自动读取 README 的游戏信息、简介、背景/世界观、人物设定、汉化目标及文体章节，保存 `config/game_background.json` 快照；技术步骤和进度日志不进入背景。每阶段的 Luna/API 任务自动携带相同背景和配置文体。主代理应核对背景完整性，根据已核实资料自动整理游戏文体；需要压缩或更正时用 game_background 字符串及 style 覆盖，不能编造未记载的设定。缺少背景章节时明确记录缺失，按原文处理。README 后续更新不会暗中改变在途提示词；暂停并通过 configure 提交 `{"game_background":null}` 重新读取，或提交修订摘要。背景改变会重置正文完成状态；影响译名时同时按术语修订规则复查。

默认整批 API 请求/结果不会写入 AINiee 的全局 config 或原 GUI 缓存。程序依赖当前本地 AINiee 修复版及技能内占位符脚本；每个运行固定其模块 hash，变更后拒绝继续，先回归再创建新运行。

## 固定阶段及质量门槛

按顺序 `prepare --stage <阶段>`；将该阶段所有任务执行并验收完成后才进入下一个阶段。

执行自定义路线前同时读取 [质量门槛与审查协议](quality_workflow.md)。新运行默认 quality_version=2，pilot_records=30（不足时用全部记录），pilot_batch_count=1，pilot_call_limit=6；通常一个 Luna 翻译、另一个独立 Luna 审校，Sol 在结束后一次验收。试译跨场景合批，保留各条元数据，不把不同场景当作连续剧情。抽样兼顾类型、风险与长度，不追求覆盖全部分支；有明确问题再定向修订，不自动扩成 150 条。

1. `extract`：覆盖全部源文，收集候选，条目保留 evidence_rows（从 1 开始的 CSV 逻辑记录号），不能凭提示词示例生成专名。结果含 analysis={characters,terms,non_translate}，采用 AINiee analysis_v1 字段。
2. `glossary_review`：程序汇总同一条目、运行共享 glossary_tool 的检查、按条目分批提供出处。任务只有分配条目，table:index 指向完整原快照。每项做语义核对，提交 reviewed_refs、review_summary、所有命中自动 finding 的处理与带依据的决定。跨批冲突由主代理汇总处理；不能把程序检查通过等同审校完成。
3. `freeze-glossary`：汇总审校决定并调用共享 build；全部检查通过才冻结正式表。无术语仍要确认全文检查为空。失败时用 `reopen-review --task <相关审校任务> --reason <原因>` 重新领取、修正、提交，再冻结；已提交任务不能直接覆盖，不编辑 DB。轮次仍受 max_attempts 限制。
4. `pilot_translate` → `pilot_review`：先 pilot-plan 检查批数与剩余预算，再执行试译及独立审校；两个阶段和重试合计最多 6 次领取，配置、分批、文体修改不刷新预算。审校采用一次六维批总结及逐条 pass，只有改动和疑难项输出详细证据。Sol 结束后一次审查，必要时用 reopen-body 给明确修订要求，不逐条往返调度；全部通过后 approve-quality --kind pilot。试译结果与文体总结保存在验收报告中，供全文任务参考。
5. `translate`：试译验收通过后，程序按 ID 分批，筛选相关术语、人物信息、保留原文要求。每个任务附术语版本与 input_hash。占位符在译前保护、提交时还原；不能手动绕开检查。
6. `review`：由不同实例全量逐条对照原文、译文、术语及上下文，提交六维 checks、source_excerpt、修改前后及错误类型。疑难项写 issues；语境例外绑定命中术语和理由。
7. 可选 `polish` → `final_review`：润色保留含义，改变时记录理由；复审实例必须与对应译文和润色产出实例不同。未启用 polish 时无需这两步。
8. Sol 最终审查：quality-report --kind final 汇总高风险文本、普通抽样、同源多译和角色口吻组。Sol 逐项核对原文、审校证据及跨批一致性，通过后 approve-quality --kind final；有错退回子代理修订并重新生成报告。不得自动批量填“通过”。
9. `export`：程序检查全部记录及当前 Sol 验收，才导出根目录单列 CSV 和 final_validation.json；实机状态记为 pending_repack。交接回填后按质量参考完成字体、排版、UI、对白及任务检查，record-game-check 保存版本、载荷与证据；release-check 通过后才发布。

任务协议、完整字段及提示词由程序 `prompt` 命令生成。不要使用 AINiee 的 textarea 输出协议处理自定义任务，也不要根据回复顺序推测 ID。

## Codex 子代理执行

### 上下文生命周期

默认不是三个实例各翻译三分之一全文，而是同时最多三个批次，每个批次使用一个新实例（fork_turns=none）。验收成功后该实例结束，下一批重新 spawn_agent；不能 followup_task 给完成的实例继续下一批。状态、译文、术语与审校证据由项目文件和数据库保存，聊天历史不作为进度或知识库。

- agent_task_limit=1：同一实际实例最多领取一个不同任务；程序会拒绝超过工作量的领取。更改 worker 别名不能代替新上下文。
- agent_attempt_limit=2：同一实例最多领取两次（包括同批恢复/重试），不是允许处理第二个不同任务。同一租约内至多一次局部格式或遗漏修复；需要再次修复时记录 fail --retry 并交新实例，仍受任务总 max_attempts 限制。
- 默认正文/术语提取最多 batch_records=60 条、batch_chars=12000 个原译文字符；术语审校和正文复审另限制 review_batch_records=20。试译使用单独 pilot_batch_count=1 合批，不受场景或正文 20 条限制；输入总预算仍检查，超限可改为 2～3 批，但总领取/重试不得超过 6 次。
- task_input_chars=48000：领取前计算完整提示词和任务 JSON 的字符数，包含背景、术语、本批文本、试译参考、占位符映射等。它是字符预算，不是模型 token 数；超限不消耗租约，暂停后缩小批次或整理经核实的背景，再准备新版本。不能截断正文或审校证据；单条超长记录需另设计保留对齐关系的切分方案。
- 每个新实例只读取自己的任务与提示词，获得相同游戏背景、文体、冻结相关术语、已确认的角色/场景元数据、本批原文及待审译文和通过的试译样例。不附带 CSV 相邻记录，不加载已完成任务的完整聊天、全库或日志；不据提取顺序推断剧情关系。
- 主代理领取时使用 claim --compact，只接收任务 ID、文件路径、租约和预算信息；完整任务包留在文件中，不反复进入 Sol 对话。子代理结束时仅报告任务 ID、结果文件路径、完成状态及简短疑难项，不粘贴整批译文或审校报告。Sol 按需读取落盘证据，继续承担调度和质量审查。

默认每批换上下文适用于所有 Codex 内部阶段。仅当用户明确希望同一实例连续处理紧密相关的小批次时才提高 agent_task_limit，同时检查 agent_attempt_limit 和完整输入预算；独立审校约束始终有效。API 请求本身已逐次使用独立上下文。调整以上调度字段使用 pause/configure/resume，不重置已完成译文。

既有运行沿用新上下文生命周期的默认限制，但已经生成的大任务不会自动改 ID 或拆分。需要重新分批时先暂停、结束/回收在途任务，再 configure 设置批次与输入预算并重新 prepare；保留已验收结果，不直接编辑任务文件或数据库。

本工作流明确要求使用子代理执行独立批次。主代理按当前可用并发容量领取任务，按用户确认的项目 model 配置运行（默认 Luna `gpt-6-luna`，`fork_turns=none`），避免继承整段聊天。若用户指定不同模型则遵从。主对话使用 Sol 负责主流程和产出物审查，不承担术语提取或正文翻译。

- 子代理负责 `extract`、`glossary_review`、`pilot_translate`、`pilot_review`、`translate`、`review`、可选 `polish` 和 `final_review` 的批次结果，以及退回后的术语与译文修订。全量逐条语义审校仍由子代理完成，主代理审查不替代该质量门槛。
- 主代理负责准备配置和上下文、领取及派发任务、租约和重试管理、调用程序验收、审查术语依据与跨批一致性、审查译文及报告、冻结术语、导出与文档交接。发现错误或冲突时写明问题和验收要求，退回相关子代理任务；不自行生成、改写或补齐批次术语和译文结果。
- 主代理审查跨批冲突并确定复核范围，具体语境核对和修订结果由子代理按任务协议提交；依然通过程序接受结果，不直接改数据库或冻结表。
- 子代理并发数按运行环境剩余槽位安排，主代理即使只做调度和审查也占一个槽位。当前会话总容量为 4 时最多并行 3 个子代理；阶段按顺序推进，批次结束后继续领取下一批，不把该数值写死为其他环境的限制。

正文、审校和润色子代理只读取任务内筛选的相关术语；术语提取与术语审校子代理读取各自分配的源文或条目。

```powershell
& $python $flow --project $project claim --stage translate --worker <spawn_agent返回的实际实例ID> --compact
& $python $flow --project $project prompt --task-file <领取返回的task_file>
```

先创建只等待任务的新子代理（fork_turns=none），取得 spawn_agent 的实际 agent ID，再以该 ID claim，生成 prompt 后通过 followup_task 发送该批任务。不能用别名、换 worker 字符串或新租约冒充新实例；程序记录并检查实例 ID，真实身份由主代理如实提供。提取与术语审校、翻译与审校、润色与复审分别使用不同实例；默认一个实例只处理一个批次及有限同批修复。派发消息包含任务、提示词、独立结果文件绝对路径及 Python 路径，要求仅生成指定 JSON，不修改原文、术语表、DB 或最终 CSV。结果写在 TranslateFiles/results/ 内自己的目录。子代理完成后主代理执行：

```powershell
& $python $flow --project $project submit --task <task_id> --token <lease_token> --result <结果JSON>
```

验收失败可让同一子代理做一次局部修复再提交；仍失败时使用 fail --retry 记账并由新实例领取，把当前结果及明确错误写入其独立任务说明，不转发整段旧聊天。最多 max_attempts 轮；关键歧义、规则缺陷和持续失败升级主代理。`wait_agent` 负责等待；任务不完整时不能结束当前工作并声称翻译完成。

领取租约即将到期时 `heartbeat --task ... --token ...`。进程重启后查看 status；确认旧 worker 已停止才 `recover --task ... --worker-stopped`。租约过期不表示 worker 一定停止，不能盲目重发。

## API 执行

第一版支持 OpenAI 兼容 Chat Completions 接口（例如 DeepSeek 或自定义兼容服务）；原 AINiee 工作流仍保留其他原生接口。凭据从用户所选环境变量读取，不放任务、配置或 Git；不要读取或展示 AINiee Resource/config.json 的密钥。

api 配置：api_url、model_name、api_key_env、target_platform、think_switch、temperature、request_timeout、max_completion_tokens、max_requests、requests_per_minute、concurrency。并发、连接工厂、服务思考参数与 SSE 解析复用本地 AINiee 改进版，HTTP/2 保持本地默认关闭。max_requests 为本次运行累计请求上限，SDK 自动重试关闭，程序分类、有上限地重试。

```powershell
& $python $flow --project $project run-api --stage translate
```

服务端错误、429 和连接错误延迟重试；认证或非重试错误暂停并报告。达到请求预算暂停，已取得译文保留。用户未选择 API 时不能自动切换过去；需要 API 凭据时仅请求设置环境变量，不让用户在聊天粘贴密钥。

run-api 只执行一个子阶段，主代理在阶段间及运行过程中调用 status 阅读报告，需要介入时 pause（停止新请求）并等待在途任务结束。API 程序可在 exec 会话后台执行并用 write_stdin 获取状态，不创建额外用户聊天。

## 调整、修复与恢复

```powershell
& $python $flow --project $project pause
& $python $flow --project $project configure --file <配置补丁JSON> --reason <原因>
& $python $flow --project $project resume
```

修改配置前必须暂停并结束/回收全部在途任务。风格、语言或匹配保护规则变化保守地重置正文完成状态；只改调度/后端/预算时保留已完成记录。任务冻结 input_hash、版本及租约，旧版本提交会被拒绝。不要直接编辑 workflow.json 或 DB 修改配置。

术语修订仍先完成全表语义审校，生成带 source/snapshot hash 的 glossary_tool 决定文件，并列出全部 reviewed_refs；暂停且无在途任务时用 `revise-glossary --snapshot <原表> --decisions <决定> --reason <原因>`。程序 build、冻结新表，比较每条记录命中的旧/新术语，仅重置受影响记录；恢复后重新执行其翻译与后续阶段。正文子代理不能自行新增术语。准备遗漏或筛选错误应当作前置阶段/工具缺陷修复。

`lock/unlock --record <ID> --reason <原因>` 保护已确认译文；解锁有记录，影响锁定译文的语义调整会被拒绝。任务返回 unresolved blocking 问题时不能验收为完成。

适合提交：配置、TranslateDoc、原术语表、审校决定、审校版、检查/修改清单、manifest、最终验证报告和必要变更记录。state、tasks、results、source 快照、cache、backups、logs 默认忽略；不提交凭据和完整缓存。
