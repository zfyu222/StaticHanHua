# 翻译流程工具

## 图片原文的准备入口

通用图片文字扫描与人工确认见 [Tools/image_text](../image_text/README.md)。该工具在解包之后运行，分开保存文字检测和OCR结果，生成疑难/未检出抽查队列、本地审核页；只导出确认后的单列原文CSV及来源坐标mapping，再交给本目录共用翻译流程。OCR候选、未检出状态和离线队列均不代表语义确认，不自动改写已有正式源文或翻译进度。

## 已停止 API 运行器的执行次数恢复

旧 API 任务达到 `max_attempts` 时，`--resume` 会按原上限拒绝重派。确认旧执行器已退出并调查具体错误后，可在暂停状态通过 `configure --file <仅含max_attempts的补丁> --reason <具体原因> --impact none` 显式提高执行上限。仅当全部在途任务均为 API、没有 reserved 请求、没有 `api_round.lock` 时允许此路径；不改变 revision、任务ID、原尝试次数或成功结果。它是有记录的定向恢复，不是自动循环追加预算；服务连通恢复也不能豁免来源证据、格式或语义校验。存在运行器锁、请求在途、Codex任务或降低上限时拒绝此路径。

## 术语疑问与有理由的通过结论

共用术语匹配器使用项目实际的占位符/禁翻规则排除保护区域内的局部命中，例如 `[Player:man/woman]` 不再拆出 Player、man 或 woman；完整标记本身作为明确术语/禁翻项时仍可命中，标签包围的正文仍正常匹配。先过滤无效区域，再沿用 AINiee 的最长跨度优先规则。保护规则及其覆盖项参与内存缓存键；正文准备、输入渲染、扫描、修复和导出使用同一规则。

启用 whole_word 时，英文直/弯撇号中的缩写片段不算独立词：Don 不匹配 don't/don’t；Don、引号内的 Don、Don's/Don’s 等所有格仍匹配。显式正则及关闭 whole_word 的子串匹配继续按用户选择执行。这是词边界规则，不靠大小写推断词义；Rest/rest 等语义歧义仍交给下述有理由的判断。匹配器版本进入草稿验收缓存签名，避免复用旧匹配规则下的误报。

程序继续检查术语，返修及审校包以 `terminology_findings` 列出本句未出现字面译名的术语疑问。子代理完整协议使用 source/expected_translation；API 使用 `term_ref` 指向本批 `shared_terms` 中的 t1/t2，回复 `exceptions=[{term_ref:"t1",reason:"具体原句语境依据"}]` 即可保留正确现译，无需抄写术语原文。撤销用 `exception_changes=[{term_ref:"t1",action:"remove",reason:"具体反证"}]`。编号只允许取本条 `term_refs`，禁止未知、跨条目、重复编号，理由必须为非空字符串；输入包与对应 session 的 `api_id_transport.v2.json` 一起绑定逐记录 `record_terms`，程序恢复为原有 source/reason 后再验收和持久保存。模板显式展示 exceptions，不要求模型强行把同形普通词译成专名。没有新增模型阶段或通过缓存表。

沿用现有记录及作者历史保存理由，无新阶段或缓存表。程序检查理由结构、术语关联及机械规则后接受；重启、重复扫描、后续修复和审校默认继承，导出也承认该结论，不因同一字面译名缺失反复返修。独立审校仍核对含义，有具体反证时用 exception_changes 显式撤销。空理由、仅返回原译文或只写顶层 reason 不产生术语通过记录；该结论只作用于本句，不豁免标签、占位符等问题。

## 一次调用完成本阶段常规多轮

`run_api_round.py --project <项目> --stage <阶段>` 与 Python `run_api()` 默认自动处理本阶段的普通循环：translate 收齐正式草稿后返回主 AI；单独调用 draft_check 执行草稿扫描及失败项修复，review/final_review 执行整轮验收及失败项下一轮。所有批次收齐后才统一检查，已通过项不重派；同一 Workflow、术语对象、内存匹配缓存和 API 客户端持续复用。无需在每轮之间返回主 AI 再启动程序，也没有新增持久化缓存表。

extract/glossary_review 完成本阶段任务及原有格式重试后交回；pilot_translate 自动生成试译报告，仍由主 AI pilot-decide；polish 仍按配置执行，结束后交回主流程。不会自动串联阶段或导出。遇到暂停、预算用尽、异常交接、无进展及 Sol/其他执行器任务时停止并返回 next_action；原次数限制和质量要求不变。API 配置中途变化也会明确停止。

正式执行顺序为 `run_api_round.py --project <项目> --stage translate` → 主 AI 检查/按需提交 Git → `run_api_round.py --project <项目> --stage draft_check` → 主 AI 决定启动 `review`。`draft_check` 复用原有草稿扫描及 translate 修复任务，不新增数据库任务类型；无问题时不调用 API，必要修复仍在一次调用内复用缓存和客户端。两次调用分别记录日志、耗时及本次 Token，项目累计用量保留；分开启动时内存缓存会重建。`draft-scan --prepare-repairs` 仍可用于仅检查和手工控制；draft_check 不使用调试用的 `--single-round`。

回执的 stage_clean 仅表示 draft_check/review/final_review 本阶段检查通过；translate 的 drafts_complete=true、next_action=draft_check 表示草稿收齐后交回主 AI，api_rounds 列出各轮摘要，last_scan 指向最后检查报告；profile 的 api_cycles 保存同一历史，时间和 Token 跨轮汇总。首轮未缓存数据仍需准备，单独启动下一阶段或重启进程也会重建内存缓存。详细范围见 [自动循环边界](../../.agents/skills/game-translate/references/api_cycles.md)。调试单轮使用 `--single-round`，或 Python `run_api(..., continuous=False)`。

## API 性能、进度与 Token 日志

真实数据准备基准可运行 `benchmark_prepare.py --project <项目绝对路径> --output <新的报告目录> --denominator 4`。程序以只读事务取一致快照，在临时夹具中取原文顺序前四分之一，复用完整冻结术语及实际配置，执行正式 prepare 和首批 API 输入生成，再测一次重复 prepare。它不调用 API、不修改原项目数据库，原 CSV 在夹具中原样复制以保留来源哈希校验；只有抽中的记录进入 prepare。报告只保留数值、配置预算及哈希，不复制游戏文本，夹具结束后清理。

每轮默认启用轻量 profile，无需额外参数，不增加轮询线程或 AI 唤醒。所有时间用单调时钟测量耗时，日志时间戳为 UTC ISO 时间。

- `TranslateFiles/logs/api_runs/<run_id>.profile.jsonl`：结构化事件流，每次请求开始/返回、失败尝试、阶段开始/结束、进度及最终总结即时落盘。
- 同目录 `<run_id>.stderr.log`：runner 保存的控制台阶段与进度日志，末尾 `[api-summary]` 一行汇总总用时、首请求等待、成功批次、失败尝试、请求数、输入/输出/总 Token、未知用量请求数及项目累计 Token。
- `TranslateFiles/reports/api_runs/<run_id>.profile.json`：最终性能与用量报告；原 runner 回执也包含路径、总用时及 performance 汇总。异常退出尽量保存失败总结；强制杀进程时以已落盘事件为准。

准备和执行阶段每推进 10 个工作单位，或有处理事件且距离上次进度超过 30 秒时输出进度，阶段开始/结束也会记录。准备按原始批组计数（另列实际 prepared_batches），执行按任务批次计数；恢复前已经完成的批次计入总体进度。单次长请求期间不另设定时心跳。

计时包含 setup/prepare/execute 阶段、术语匹配、Token 计算、输入生成、任务分配、配置校验、数据库锁等待、派发锁等待、限流等待、API 请求、结果收集及重试等待；timings 保存调用数、累计耗时和最大单次耗时，counters 包含术语缓存命中/未命中次数。嵌套计时存在重叠，并发请求累计耗时可能超过总墙钟用时，不能相加当总用时。

`run_tokens` 为本轮真正发出的请求用量，包含格式错误、空回复及重试中服务已报告的用量；离线恢复旧回复不重复计入本轮。`project_tokens` 汇总数据库中整个项目各阶段的历史请求，包含本轮，不应再与本轮相加。用量以服务返回为准，未返回的统计为 unknown_requests，日志中的已知 Token 合计可能低于实际消耗。缓存输入及推理 Token 单独列示，但属于输入/输出 Token 子集，不重复加到总量。profile 不记录原文、译文、请求正文或密钥；运行日志与回执不作为正式交付文件提交。

## 简化 API 协议与接续

正文 API 输入使用批内短 ID `1`、`2`…（JSON 字符串），模型只返回 records 和阶段所需内容，程序补齐任务 ID 与哈希；编号映射保存在 `api_id_transport.v2.json`。占位符使用 `[P1]` 等短标记，原文碰撞时自动换前缀。未知、重复编号仍拒收，正式审校证据仍须完整。

术语提取仅返回 `analysis`；术语审校仅返回 `decisions`，任务及来源/快照哈希由对应 session 绑定。审校条目使用 `e1` 等短引用，冲突使用 `f1` 等短引用；`edits/quarantine/allow_no_match` 引用分配条目，关联条目只读。程序从完整且不重复的 `entry_reviews` 恢复 `reviewed_refs`，不代填四维证据、完成声明或冲突确认。输入保留候选、提案次数、出处与语义审校理由，过滤提取任务 ID 等追踪字段。新增术语仍须给出实际类别、完整内容及依据。

独立审校、润色和润色后复审未改译文时返回 `keep:true`，无需重抄原译；修改时只输出一次完整 `translation`。审校的 `changes` 只需分类、严重度、摘录和原因，程序绑定原译与新译补齐 `before/after` 及审校标记。`keep` 不是语义通过结论：六维检查、理由、原文摘录和问题仍由模型真实提供，并通过共用验收；缺字段不等于保留，错误的显式前后文仍拒收。原始回复和还原后的完整结果分别保存，中断恢复使用同一 session 映射，不重调已完成请求。

程序每轮只执行一次任务准备；缓存已规范化的冻结术语、逐句匹配结果及未变化文件的来源校验。缓存仅驻留内存且有容量上限，重启可重新准备；已完成任务按数据库状态跳过，不重新翻译。未增加本地工作线程，保留已有 API 并发。

旧执行器停止后，接续命令：

```powershell
& $python Tools/localization/run_api_round.py --project '<项目绝对路径>' --stage translate --resume
```

`--resume` 检查遗留 runner 的父子进程均已结束，先收集保存的完整回复，再重试没有可用回复的未完成批次；保留原尝试预算。已成功提交的批次不重做。旧版在途协议不兼容，勿对旧交接直接使用新映射；重开项目应由正常初始化入口执行，不直接改库。尚未保存回复的请求可能需要重发。

缺少回复的返修子包恢复时，沿用原任务租约，只将该子包转回待发；已保存的兄弟子包、通过对照及候选不重做。原始attempt与请求历史保留；有落盘回复仍优先收集。仅完整JSON漏掉完整translation、没有其他错误时，补全任务按正常输入/输出预算合批，不因尝试较多就必拆成逐条；输出截断仍缩小范围，累计上限及验收不变。

局部性能对比（2026-10-18）：Expeditions Viking 冻结术语 3,529 项、分散抽样 80 条，原逐句匹配 3.437 秒，新首次匹配 2.588 秒、缓存复用 0.0017 秒，逐项结果一致。该数据不代表全文启动或 API 服务耗时。

翻译仅提供 SubAgent 和 API 两条路线，程序入口是 `translation_workflow.py`；路线选择及子代理派发由 `game-translate` 技能管理。所有翻译中间产物放在游戏汉化项目 `TranslateFiles/`，正式原文/译文保持项目根目录的单列 CSV 格式。

API 和 SubAgent 共用一套流程与程序，以既有 SubAgent 规范为准。唯一流程见 [自定义流程](../../.agents/skills/game-translate/references/custom_workflow.md)，统一输入产出和修复见 [批次交接](../../.agents/skills/game-translate/references/batch_execution.md)。两种方式均由 batch-open 生成 input.md 和模板、由 batch-finish 接收 result.json；API 只将相同输入包发送为单次请求，程序解析回复并落盘，不启用 agent 工具循环。升级修复保留 Sol SubAgent。修改共同模块即同时作用于两种方式，无需维护第二份实现。

API 正文展示使用 `shared_terms` + 每条 `term_refs`，术语内容同批只传一次；省略无用途的 `ordinal` 及空保护/元数据/锁定字段。各阶段仅附输出结构示例，完整范围由输入记录 ID 或 `entries.ref` 定义，回复仍须全部覆盖。审校模板不再逐条重复原译文和空证据，试译不设置独立审校；正式审校回复须提供逐条六维证据，术语审校提供逐项四维证据。`reason` 简述结论、`checks.evidence` 分维举证、`changes.reason` 解释改动，避免重复整段说明。术语阶段过滤调度及无用空字段，保留原文序号、候选、出处及关联冲突信息。原文/保护映射、锁定和修复约束保持完整。任务存储、完整结果模板、哈希、结果协议、验收和既有在途文件不变；预算仍沿用共用的保守输入估算。

## 运行

API 新交接使用精简提示词，去掉子代理工具/文件操作、轮询、调度和重复协议说明；保留游戏背景与文体、本批数据、相关术语、保护和语义审校要求、输出模板。与 SubAgent 共用数据与结果协议、保守拆批预算和质量门槛；已生成的在途输入包不改写。精简的是执行说明，不截断背景、原文或审校证据。

自定义术语提取结果在首次生成审校快照时，按英文原词忽略大小写及首尾空白合并。一个原词即使有多个译名或分类，也只占一个审校名额；全部出处去重汇总，候选及各自出处保存在 extraction_candidates。暂定译名按提取提案次数最多者选择，同票取首个，仍需语义审校确认。任务输入包含每个候选的代表性出处；单个合并条目超限时分配阶段报错，不拆成多个审校任务。既有审校快照及在途任务不自动改写。

复用本地 AINiee 的 Python 3.12 环境及修复版模块，不另外安装同一套 SDK：

```powershell
$python = 'D:\StaticHanHua\Tools\AINiee\venv\Scripts\python.exe'
$flow = 'D:\StaticHanHua\Tools\localization\translation_workflow.py'
$project = 'D:\StaticHanHua\Projects\GameName'
& $python $flow --project $project init --source "$project\gamename_text.csv" --config "$project\TranslateFiles\config\selection.json"
& $python $flow --project $project status
```

selection.json 为配置补丁。例如：

```json
{
  "workflow": "custom",
  "backend": "codex",
  "model": "gpt-6-luna",
  "source_language": "英语",
  "target_language": "简体中文",
  "batch_records": null,
  "review_batch_records": 80,
  "review_output_tokens": 16384,
  "batch_tokens": 1024,
  "extract_batch_tokens": 10000,
  "task_input_tokens": 16384,
  "context_records": 0,
  "polish": false
}
```

已有配置恢复时不重新 init。`--context` 可读取提取时的上下文映射：JSON 对象按源文逻辑记录号索引，值可包含 group、speaker、resource、scene、text_type、note；各字段为字符串。术语提取按 group 分批；正文翻译与审校可跨 group 合批，每条保留已核实元数据。speaker 可让程序补充相关人物术语。context_records 仅为兼容字段，即使旧值为 2 也不附带相邻 CSV 记录。

术语语义审校默认每批最多 60 个条目（glossary_review_batch_records=60），同时受 glossary_review_batch_tokens=10000 和 task_input_tokens=16384 限制。既有配置保留显式旧值；调整时使用 pause/configure/resume，不直接修改运行配置或在途任务。

词族候选由 glossary_families.py 在本地生成：识别英文书写/部分单复数变体、保守派生词根及完整词组件，尽量同族同批，保存 glossary/r{revision}/family_candidates.json。不新增模型请求阶段，不自动改译名，也不回扫全文补提取。每批 term_families 最多 120 条关系提示、12 个跨批精简关联词（不重复附带其原文出处）；原有自动 finding 关联证据保留。审校在既有 consistency 证据中判断核心译名统一及语境差异，无须额外词族报告。候选可能误报或漏报，非同义结论，非通用词形分析器；已有任务及冻结表不自动变更。

草稿扫描支持 `draft-scan --workers 4`，本次调用临时使用 4 个校验线程；也可在配置补丁中设置 `"draft_scan_workers": 4`，通过现有 configure 流程持久保存。线程数范围为 1～32，默认 1；短文本正则匹配实测可能因线程调度变慢，应按项目测试后选择。扫描复用已读取的记录快照，每条记录只匹配一次术语。工作线程不访问 SQLite、不写文件，主线程按原文顺序汇总并保存结果；排队任务最多为实际线程数的两倍，线程异常会中止扫描并回滚数据库事务。

init 默认从项目 README 的游戏信息、简介、背景设定、世界观和文体等章节自动读取游戏背景，保存到 TranslateFiles/config/game_background.json。Luna 与 API 的每阶段任务都会携带这份快照、文体及相关上下文。可用 game_background 字符串替换为已核实的背景摘要；设为 null 则自动读取，设为空字符串则不载入背景。运行中更新 README 后需暂停并 configure 重新读取，不会自动改变在途任务。背景缺失时不推测剧情。

配置保留 `workflow=custom` 作为共用流程的历史字段，路线仅通过 `backend=codex`（SubAgent）或 `backend=api` 选择。`workflow=ainiee` 已停止支持，初始化和配置修订会拒绝该值。旧 GUI 项目的缓存和译文不自动迁移或覆盖，需先选择现行路线并核对产物。AINiee 目录仅作为现有复用模块、运行环境与 StevExtraction 的依赖保留。

## 程序接口

| 命令 | 用途 |
|---|---|
| init / status | 保存选择及快照、占位符分析；查看进度与异常任务 |
| prepare --stage ... | 按阶段生成任务，重复调用不重复创建已覆盖任务 |
| claim --stage ... --worker ... | 事务领取任务、生成独立任务文件与租约 |
| prompt --task-file ... | 为已领取任务生成协议提示词文件 |
| submit --task ... --token ... --result ... | 按阶段协议保存结果；正文和独立审校候选需在整轮结束后统一验收 |
| batch-open / batch-finish / batch-repair / batch-retry | 两种执行器共用全部阶段交接；batch-repair 用于条目可靠性问题，batch-retry 重发传输/整体协议错误 |
| batch-split --task ... | 拆分未尝试、无交接的首轮或修复审校任务，同时检查输入和输出预算 |
| pilot-plan / pilot-report / pilot-decide / pilot-retry | 随机试译、诊断报告、主 AI 整体判断与指定范围重试 |
| quality-report / approve-quality | 正式译文质量报告和最终验收；kind=pilot 兼容转入校准协议 |
| draft-scan / review-scan | 整轮草稿或审校完成后统一验收，失败 ID 跨批合并修复 |
| sol-round-record / sol-round-finish | 固定范围裁决，全部结论齐备后统一派修或批准 |
| record-game-check / release-check | 保存回填实测及核对发布载荷 |
| freeze-glossary | 合并所有审校决定，复用 glossary_tool 构建后冻结 |
| reopen-review --task ... --reason ... | 冻结前修正已提交术语审校；保留轮次限制与历史 |
| fail / recover / heartbeat | 失败记账、核查旧 worker 后恢复、延长租约 |
| pause / resume / configure | 停止新任务、恢复、通过配置补丁创建新修订 |
| revise-glossary | 带来源哈希的术语语义审校完成后冻结修订，仅重置受影响记录 |
| lock / unlock | 有理由地保护或解锁确认译文 |
| run-api --stage ... | 程序执行 API 任务；并发、限流、延迟重试及请求预算 |
| export | 必需阶段和最终检查通过后原子导出，并备份已有正式译文 |

阶段顺序及质量门槛统一维护在 [自定义流程的固定阶段](../../.agents/skills/game-translate/references/custom_workflow.md#固定阶段及质量门槛)，本文件仅列程序入口和配置，不复制 API 或 SubAgent 流程。

Codex 子代理由主代理通过平台工具调用，普通 Python 不负责调用聊天内的 spawn_agent。API 执行复用 AINiee 的客户端工厂、平台思考参数和 SSE 解析；本版支持 OpenAI 兼容接口。未接入的其他原生接口不属于当前翻译路线。

API 地址、密钥和模型统一填写本地文件 `Tools/localization/api.local.jsonc`（已排除 Git）。首次可复制 `api.example.jsonc`；所有自定义 API 项目共用，下一次 run-api 启动读取，不改写在途请求。项目仍需选择 backend=api，不再逐项目重复填写连接信息。

配置采用 JSONC，支持中文 `//` 行注释、`/* */` 块注释和尾随逗号；本地文件及示例已逐项写明参数用途、单位和默认含义。字符串中的网址、斜杠和注释样式文本保持原样。

同一文件可定义多个 API：`apis` 下每个键是一个可切换的 API 名称，`default_api` 指定默认使用哪一项，单次运行可用 `run-api --api <名称>`（或 `run_api_round.py --api <名称>`）临时改用它项。名称仅作标记，不能为空；`default_api` 指到未定义的名称时报错并列出已定义名称。

```json
{
  "default_api": "relay",
  "apis": {
    "relay": {
      "api_url": "填写服务地址",
      "api_key": "填写密钥",
      "model_name": "填写模型名称"
    },
    "official": {
      "api_url": "填写服务地址",
      "api_key": "填写密钥",
      "model_name": "填写模型名称"
    }
  }
}
```

每个 API 条目内可写 api_url、api_key、model_name、extra_body、target_platform、stream、think_switch、temperature、concurrency、requests_per_minute、max_requests、request_timeout、max_completion_tokens、tls_switch；与具体服务无关的字段也可写在顶层作公共值，条目内同名值优先。`batching` 与 `default_api`/`apis` 只在本机解析，不并入请求参数。未填写的参数沿用项目配置。只定义一个条目时可省略 `default_api`，定义多个又未指定默认项则报错，不随机挑选。

不需要设置环境变量；为兼容旧项目，本地文件不存在或三项连接信息全部为空时，仍沿用原项目设置与 api_key_env；旧版平铺写法（顶层直接写 api_url/api_key/model_name）继续有效。选中条目缺字段则报错，不混用另一来源的密钥或地址。密钥仅在内存中用于请求，不写入项目配置、任务或历史；报错信息只列字段名，不回显密钥。不要提交或分享 api.local.jsonc。

请求预算累计跨阶段，不因重启或切换API清零；达到预算暂停。SDK隐式重试关闭。未启用下列优先级配置时沿用原故障停止规则，不自动改用另一服务。代码模拟测试不使用真实凭据；有界真实验证另行记录。

### API优先级与故障记录

可在本地`api.local.jsonc`添加：

```json
"api_failover": {
  "enabled": true,
  "priority": ["yjapi", "vortex", "acy7", "deepseek"],
  "failure_threshold": 5,
  "cooldown_seconds": 60
}
```

名称必须已存在于`apis`。启用后每次派发都从`priority`首项重新选择（取代`default_api`），失败任务重试也一样；`--api <名称>`固定该接口，不派给其他家，但已启用的冷却恢复机制仍生效。配置下次启动生效，不改写在途请求。正文、术语和审校共用`workflow_api_failover.py`，本游戏专项适配器也接入同一模块。

各接口分别维护四种状态：`normal`正常、`disabled`暂时停用、`retry_ready`待重试、`awaiting_retry`等待探测结果。正常时连续5次API服务错误（连接/超时、断流、HTTP错误、空响应等）转为暂时停用60秒；正常请求成功清零连续失败数。取消2/4/8秒探测，不在第1次失败后隔离。到期转待重试，只允许一个真实待办请求取得探测资格，原子变为等待结果，其他任务跳过它选择后面的正常/待重试接口。探测成功立即恢复正常并清零；探测失败立即重新冷却60秒。

每次选择都按优先级重查，因此便宜接口恢复后会重新使用，不会粘在低优先级。正常接口只是达到配置并发/限速时等待该接口，不仅因忙就升高API价格。旧请求晚到的成功/失败不能改写新一轮冷却或探测状态，取消且未真正发出的探测不能算恢复成功。全部接口都在冷却/等待结果时等待最早的探测机会，不新发空探活，不直接终止；公共pause可中断等待。模型输出预算耗尽、JSON/译文/语义校验不合格仍由原质量规则处理，不当作API服务错误。

每家API的并发、限速、模型和思考参数均读取该条配置；仅冷却后的重试状态限一个探测，正常接口直接按配置并发派发。普通多轮复用同一状态管理器；新阶段/进程重新初始化状态。每次execute只有一次SDK调用，各次重试/切换分别保留原账目，不重派已通过项，不放宽四维/六维与来源校验。服务故障仍不占模型请求预算，已知Token照记、未知不猜零；切换不能绕过原累计请求上限。

每次阶段产出`TranslateFiles/logs/api_failover/<run_id>.jsonl`和`reports/api_failover/<run_id>.json`。逐请求记录API名、请求/响应模型、主机名、阶段/任务/交接ID、探测状态、时间、耗时、结果与错误类别、服务端用量；逐次记录选路与四种状态变化、原因、连续失败数和冷却秒数。密钥、URL认证信息、请求头、正文和模型完整回复不进入这些统计日志，原始回复仍按既有任务目录保存。

汇总包含按API的请求量、接口返回率、服务错误分类、在途峰值、耗时、收集失败和输入/输出/总/缓存/思考Token及字段覆盖率。返回率只表示传输可用性，不代表译文准确率；缓存和思考是子集，不额外加进总Token。缺失用量为null/未知，终止进程留下的未完成请求单列，不推断为成功/失败。

跨阶段、含异常中断记录的复盘可运行：

```powershell
Tools/AINiee/venv/Scripts/python.exe Tools/localization/api_stability_report.py --root Projects/<game>/TranslateFiles
```

输出`reports/api_stability_summary.json`。也可把root指向某个隔离工作目录，仅统计该范围。汇总重放逐请求日志、对重复run/call去重，不把多轮重试Token漏记，不累加嵌套/并行耗时冒充墙钟时间；不将未留接口日志的旧请求倒推给当前API。

同一 api.local.jsonc 的 `batching` 对象集中设置拆批参数，完整示例见 api.example.jsonc：

| 字段 | 含义 | 默认值 |
|------|------|--------|
| batch_records / review_batch_records | 正文翻译、润色 / 独立审校条数上限 | null（不限） / 80 |
| extract_batch_records / glossary_review_batch_records | 术语提取 / 术语审校条数上限 | null（不限） / 60 |
| batch_tokens | 正文翻译、试译、润色的原文 Token 预算 | 1024 |
| extract_batch_tokens | 术语提取的原文 Token 预算 | 10000 |
| glossary_review_batch_tokens | 术语审校候选条目的 Token 预算 | 10000 |
| review_batch_tokens | 独立审校原文、译文及问题描述的 Token 预算 | 1024 |
| task_input_tokens | 完整输入包的额外 Token 安全预算 | 16384 |
| review_output_tokens | 审校预计输出 Token 总预算 | 16384 |
| review_output_reserve_percent | 输出预算预留百分比 | 20 |
| review_estimate.full_base_tokens | 正式审校每条固定开销（pilot_base_tokens 仅兼容保留） | 420 |
| review_estimate.ascii_chars_per_token | 每多少 ASCII 字符估作一个 Token | 3 |
| review_estimate.non_ascii_tokens_per_char | 每个非 ASCII 字符估作多少 Token | 2 |
| review_estimate.translation_multiplier | 译文长度的估算倍数 | 3 |

Token 计数复用 AINiee CacheItem 的 o200k_base 及其降级估算，不是 MiMo 专属 tokenizer。正文和润色的 1024、提取的 10000 对齐当前 AINiee；完整输入包的 16384 是自定义流程额外安全预算。batch_chars/task_input_chars 仅兼容旧项目，不再参与拆批；当前配置文件已移除它们。试译只复用正文翻译拆批和草稿保存，固定随机 150 条，由主 AI 整体校准；pilot_batch_count/pilot_call_limit 停用，旧值不限制任务或执行次数。单条原文超过内容 Token 预算时完整保留、单独成批；完整输入或预计输出单项超限仍报错。

run-api 在拆批前同步 batching，通过现有 configure 修订机制只更新调度参数，保留已完成译文和质量结论，未完成任务按新预算准备；存在在途任务时拒绝变更，先结束或恢复它们。程序只持久化 batching 中的非敏感字段，密钥不进入修订记录。改文件不改变正在运行的请求；下一次 run-api 启动生效。省略字段沿用项目配置。review_output_tokens 仍不得超过 API 的 max_completion_tokens。估算系数不改变审校证据要求，也不保证实际回复一定不超限。

最大并发只配置 `api.concurrency`；默认值、请求速率与修改方式统一见 [API 并发配置](../../.agents/skills/game-translate/references/custom_workflow.md#api-执行)。`test_api_concurrency.py` 用阻塞式模拟请求验证实际并发峰值、项目覆盖值和并发下的累计请求预算。

术语提取与术语审校的 API 输入只列出本批随附原文的证据行号：`compact_term_input` 会按 `records` 裁剪 `evidence_rows`与`corpus_evidence_rows`，条目自身原提取出处若一条都不可见则保留原样，只读关联项和候选列表裁空不留。程序侧 payload、任务哈希与验收门槛不变，因此无需重开任务；被隐藏的行号本来也无法引用或核验。出处比对在 `quote_matches` 中忽略空白与换行差异，引文文字仍须逐字连续，改写、拼接和省略号照旧拒绝。`test_glossary_input_fixes.py` 覆盖基础引用边界。

新审校快照在绑定哈希前，通过`glossary_corpus_evidence.py`本地检索全文定义，最多每词补两条完整原文；可从明确“原词+类别”再找一层类别说明，总数不增长。检索不推定词义、不使用CSV邻接关系，不新增模型阶段，现有输入预算与拆批继续生效。确认含义由审校者写入已有`term_reason`并沿匹配术语传入正文和独立审校；相关出处仍须真实引用。当前定义提示词以英语为限，不能声称穷尽所有语境；旧审校/冻结快照不自动更改。规则见[从全文取得定义](../../.agents/skills/game-translate/references/glossary_review.md#从全文取得定义避免短标签丢失语境)。`test_glossary_corpus_evidence.py`验证远处定义、同形干扰、类别关联、完整出处、可见引用及旧快照稳定；`live_game_feedback_probe.py --source-only --output Development/<新目录>`在隔离副本中做有界真实API回归，历史错误译文为复制夹具，非模型产出或语义通过结论。

完整短标签因literal_context范围未命中词条时，`label_meaning_hints`仍传递精确同名词条的已审含义，避免单独分批后丢失语境；它是只读提示，不扩大术语范围或产生例外。该查找复用冻结快照的内存索引，不对每条重新扫描全表。无截图回归的真实通过与失败范围见[报告](reports/source_context_20261024.md)，其中“胡茬的头皮”仍被未接收候选漏检，不能宣称独立审校已消除此类错误。

审校策略开发应另查[常识与词义对照验证](reports/review_plausibility_20261024.md)：程序接收的六维结果仍可能有明确语义错误。`live_review_plausibility_probe.py`在新Development目录中测试真实坏例、正确译法及原文明说的奇异情形；baseline/candidate最多各两次出站请求，source_only最多一次。candidate只在开发脚本内启用归档的失败实验，不改变正式提示词；策略或模型测试须报告漏检与误改，未改善时不能把程序通过当作优化有效。本次任务实际合计四次请求，不自动启动这些测试或增加正式游戏阶段。

`source_reference --reading-report <Development中的实际盲核报告>`可在独立开发夹具中将API原文独立译法作为未审定参考，再审校旧译，最多一次出站请求；校验六条原文和记录号对应，不将参考认定为正确答案。接口恢复后的[接续结果](reports/source_blind_resume_20261024.md)仅确认独立生成能选对本组目标词义，后续审校请求超时，不能宣称闭环已通过，也未自动用于正式游戏。

后续通过公开resume接续的[审校结果](reports/source_reference_resume_20261024.md)已识别两处真实胡茬错误和一处头发状态错误，并保留三条正确对照；但修改项缺完整translation，只有一个修订候选已补全，另两项仍待补全，不能宣称整批通过。`test_source_reference_reply.py`重放真实缺字段回复及完整修订候选，确保不从修改理由或独立参考中代填译文。

### 自动词义审校

常规review/final_review自动处理全部分配语句，不要求玩家先读全文挑疑点。共用提示先确定原文的动作参与者、修饰对象和词义再对照旧译，并用头皮短发茬、下巴胡茬及原文明说头皮长胡须作正反例：词义/关系错属于accuracy，不能用术语字面命中或奇幻背景开脱；正确译文和明确设定应保留。规则泛用于其他对象和多义词，不新增关键词替换表、输出字段、全文释义或模型阶段。新生成任务默认使用，已有在途文件不改写；必要拆批继续服从原输入/输出预算。

这是现有全自动审校的职责；下述独立解读导入只是辅助诊断工具，不是要求用户人工筛选的前置步骤。提示强化不保证零漏判，不能把机械通过当语义全对。真实反例和未写入提示的词义对照见[验证报告](reports/review_prompt_contrast_20261024.md)。

`live_review_plausibility_probe.py`支持`--api <本机命名API>`，仅选择本次隔离测试的接口，不修改default_api、并发或凭据。固定同一提示与样本才能比较供应商，记录实际消息哈希、思考参数、回显模型与usage，不凭名称鉴定转发后端。2026-10-24的[yjapi/官方对照](reports/api_provider_comparison_20261024.md)各两轮都修正5条错误并保留5条正确译文，未观察到质量差距；这是小样本结果，不是全游戏准确率或接口真实性证明。

### 可选原文独立解读与回复可靠性

2026-10-24后续[流程优化验证](reports/review_optimization_20261024.md)补齐剩余两条，原六条整批审校通过。新API示例对多条审校分别展示完整修改与keep路径；单条示例及文字说明同样要求依真实判断选择，不因示例ID或示例改动类别推定结论。修改项须返回完整translation及本条来源，程序不从reason、changes或参考中推造译文。

新增`import-review-readings`，只把另一个已完成的原文API试译作为指定ID的未审定参考。默认禁用，不自动发起额外盲译；导入本身零请求，参考生成和随后审校仍各有实际费用，来源项目预算单独保存，执行者须限制并汇总。来源须保留真实API接收及源文/任务/结果绑定，不含旧译、候选、逐条元数据或译例。目标需暂停、无在途/待办、所选ID未锁定且同属translate或review阶段；已完成review仅重开指定ID，预算耗尽不能导入绕过停止条件。

```powershell
& $python Tools/localization/translation_workflow.py --project '<目标项目绝对路径>' import-review-readings --source-project '<原文独立试译项目绝对路径>' --ids '<ID1>' '<ID2>' --reason '核对指定记录的词义分歧'
```

参考经共用分配器绑定原文、计入预算，API与SubAgent均可读取；不改在途包，不直接替换译文，仍须完整审校和整轮验收。当前样本带参考和无参考均能修对，不能据此声称参考更可靠或默认应当采用。完整边界见[流程说明](../../.agents/skills/game-translate/references/quality_workflow.md#可选的原文独立解读参考)。`test_review_readings.py`、`test_review_omission_recovery.py`及真实回复回归覆盖范围、锁定、来源、预算回滚、遗漏补全与部分接续。

## API 阶段执行与等待

API 自动执行统一通过 `run_api_round.py`，由新的 Luna 执行代理管理一个阶段的常规多轮；主代理用原生 `wait_agent` 等待终结事件，不轮询进程或运行日志。具体派发模板及边界见 [API 阶段执行与等待](../../.agents/skills/game-translate/references/api_execution_wait.md)。

```powershell
& $python D:/StaticHanHua/Tools/localization/run_api_round.py --project $project --stage translate
```

包装器只启动一次 run-api，阻塞等待本阶段普通循环结束，日志留在 TranslateFiles/logs/api_runs，回执留在 TranslateFiles/reports/api_runs；工具只收到一次 JSON。translate 只保存正式草稿并交回主 AI，单独启动 draft_check 后才开始程序审校；draft_check/review/final_review 的整轮检查和常规修复由程序自动推进，阶段切换和升级由主流程决定。completed 代表程序正常交回，stage_clean 表示这些阶段相应检查通过，不代表项目可以发布。执行代理只返回状态、计数、停止原因和回执路径，不把全文日志加入主对话。

state/api_round.lock 防止重复启动；异常遗留锁及未知在途交接必须先核实旧进程停止，不自动回收或重放。等待超时只续用原生等待，不追加状态查询；收到用户新指令时按其要求处理。原生完成通知来自执行代理，回执文件本身不会唤醒 Codex；不承诺绝对零唤醒或零 Token。

## Sol 只处理遗留争议和升级难题

正文最终 `quality-report --kind final` 使用 `disputes_only_v1`：只把独立审校明确遗留的 issues 交 Sol 裁决。风险标签、普通抽样、历史改动、同源多译及角色口吻组不自动升级；试译验收保持。已修正并独立复查通过的内容不重复交 Sol，多轮仍失败或反复回退的记录才按既有模型路由升级为新 Sol 子代理任务。

旧大范围轮下一次 quality-report 自动归档并收窄，实际通过结论按内容签名复用，已有 repair 结论保留为 carried_findings 后集中交修，不重读、不静默放行。remaining_checks=0 时执行 sol-round-finish：有已确认问题则派修，无争议无问题则完成程序质量闭环，不编造模型逐项通过。coverage.risk_records 是统计，coverage.sol_records 才是争议选择量；导出仍要求全部独立审校及当前质量闭环通过。

## 复用与验证

AINiee 基线、模块 hash 和来源记录在各运行的 source/manifest.json；本地术语匹配修复直接复用，保护规则复用原占位符分析器，标记还原复用 TextProcessor。新增不碰撞的保护编号和不限 50 个的标记处理，避免旧处理器的批次数量限制。术语审校复用 glossary_tool.audit/build。

本地 AINiee 为 AGPL-3.0，保留其 `Tools/AINiee/LICENSE` 和来源说明。适配器无全局配置读写，未改动 AINiee 源码。

```powershell
& $python -m unittest discover -s Tools/localization -p 'test_*.py' -v
```

测试覆盖并发领取、来源与版本变化、旧结果拒绝、完整术语前置门槛、审校门槛、单列 CSV 对齐、锁定译文、增量复查、保护标记及模拟 API 预算。真实 Luna 并行验证报告见 verification/luna_smoke_report.json；样本为合成文本，不代表已验证所有游戏语境。真实 API 请求尚未测试。

运行队列和大缓存默认忽略；项目配置、术语审校材料、必要变更记录和交付报告可以入库。技能参考 `references/custom_workflow.md` 承载具体 AI 调度规则，基础文档无需列出内部子阶段。


## 修复计数与停止条件

普通修复按语句 ID 跨 translate、review、final_review 和所有 revision 累计，最多 6 轮后升级 Sol；同一任务的格式/传输重试不增加修复轮数。修复任务标记 repair_budget_scope=record。历史任务和旧阶段计数作为下限保留，已完成任务即使被标记 obsolete 也计入，不因 configure、拆批或换阶段刷新。

Sol 修复默认累计最多 3 轮（sol_repair_round_limit），同一条目连续 2 轮 Sol 检查得到相同的未解决诊断（sol_no_progress_limit）也停止该条自动派发。停止按条目生效，其他条目继续；通过项不返工。报告返回 needs_attention_ids、repair_stop_reasons（sol_round_limit / sol_no_progress），保留候选、证据及计数，不能批准或导出未解决问题。主流程应核对原文、术语语境和校验规则根因，不通过改任务 ID 或调整配置重置预算。配置修改不清除停止历史；确有规则缺陷应修正规则并复核保存的候选，通过后再继续，不能机械追加模型轮次。

## 共用批次预算与执行职责

审校输出估算与拆批规则统一维护在 [审校输出预算](../../.agents/skills/game-translate/references/review_rounds.md#审校输出预算)。API 与 SubAgent 使用相同预算和批次算法；API 容量配置见 [自定义流程](../../.agents/skills/game-translate/references/custom_workflow.md#api-执行)。

`workflow_allocation.py` 是统一任务分配入口，`workflow_batches.py` 负责两种执行器共用的交接、收集和可靠性修复。输入准备、输出模板、真实语义工作及程序验收的分工统一见 [准备与验收职责](../../.agents/skills/game-translate/references/custom_workflow.md#程序准备任务子代理专注本职)，不在工具说明中复制规范。

试译执行顺序：`prepare --stage pilot_translate` → 执行本轮全部翻译 → `pilot-report` → 主 AI `pilot-decide --file <JSON>`。决定只需 report_hash、reviewer=main、decision=accept/retry/stop 和 summary，retry_ids 可选。评估流程是否稳定、风格是否合适及是否存在共性错误，不要求逐句六维证据或错误清零。没有 API 独立试译审校、额外 Sol 验收或自动 6 轮逐句返修。

需要改进时通过配置入口调整，再用 `pilot-retry --ids <ID...> --reason <原因>` 验证受影响样本；省略 --ids 表示同批全部。旧译文、作者与报告保留，不重抽样。每轮报告位于 reports/pilot_rounds/，主 AI 判断位于 reports/pilot_feedback/，当前报告为 reports/pilot_calibration.json。

接受试译后恢复生产状态，正式正文重新翻译并完成完整独立审校；试译接受不放宽最终导出或实机门槛。旧已接受试译继续有效；旧在途请求先结束或核实恢复。pilot_review 派发入口已停用，兼容 draft-scan --stage pilot_translate 只生成诊断，不自动创建修复任务。
# API 格式可靠性

各自定义阶段共用相同传输、收集与恢复入口，具体覆盖如下：

| 阶段 | 无损编号规范化 | 格式重试输出约定 | 回复诊断 |
|---|---|---|---|
| extract | 输出 analysis，无记录编号；出处序号不改 | analysis | 共用 metadata、usage、结束原因及日志 |
| glossary_review | 已绑定 e/f 编号；只读/未知/重复仍拒收 | decisions 与真实四维证据 | 同上 |
| pilot_translate、translate | 记录 ID 与本条 t 编号 | 每条完整 translation，不使用 keep | 同上 |
| draft_check | 修复复用 translate；无问题时不请求 | 同 translate | 同上 |
| review、final_review | 记录 ID 与本条 t 编号 | keep 或 translation，真实六维证据 | 同上 |
| polish | 记录 ID 与本条 t 编号 | keep 或 translation，真实修订理由 | 同上 |
| resume | 只使用原 session 映射，不猜当前全表 | 先收完整回复；确需重发时带恢复诊断，保留累计次数 | 恢复 parsed 与缺失 metadata；未知保持未知 |

整数转换仅适用于明确命名空间、已绑定范围内的正整数（e/f/t 分别依据 ref、finding、term_ref 字段）；布尔、浮点、未知值及重复仍拒收，不转换语义文本或 evidence_rows。格式反馈按阶段选择输出约定；错误中的长标识仅按完整边界还原成本批短编号，避免 terms:1 被误匹配到 terms:12。

隔离真实验证入口：`verify_api_stage_reliability.py --directory <全新验证目录> --phase prelude`，执行提取、术语审校和试译后等待主 AI 实际阅读并接受试译；随后 `--phase body` 执行翻译、draft_check、review、polish 和 final_review。总请求上限 12，保留原回复和真实验收，不使用验证结果覆盖生产项目。各阶段不会因此在正常生产流程自动串联；该入口仅用于明确授权的开发验证。

- API 正文短编号可以返回字符串，或范围内的正整数；程序只无损转为已绑定的字符串编号。布尔值、浮点数、越界编号及规范化后的重复编号拒收，不按输出顺序猜测。
- 格式重试将上一交接的具体错误、JSON 行列及有限回复片段作为诊断数据附入新输入，仍受原任务次数和完整输入预算约束；超预算明确停止，不截断源文或证据。旧输入、原回复、映射和真实作者不改写。
- 原始回复旁保存 `response.metadata.json`，记录服务端 usage、finish_reason 及返回模型。SSE 回退无法取得结束原因时保存 null，不编造 stop；未知用量不记零。
- 已确认接口兼容后，可在本机配置的 `extra_body` 中设置 `"response_format":{"type":"json_object"}`。JSON 模式只保证服务端承诺的语法形式，不保证 ID、引用、keep 或语义证据正确，所有共同验收仍运行；不支持时明确报错，不静默切换模式重发。
- 不用通用“修 JSON”算法补写证据、keep 或译文。普通语法损坏仍走带具体诊断的有限重试；持久化的失败回复优先审计恢复，避免重复请求。

## 2026-10-19：回复可靠性与失败隔离

- review/final_review 统一执行收集前协议检查，分配契约为 4；本次不增加旧任务兼容分支。收集时逐条检查 ID、译文字段、六维状态/非空证据、连续摘录及修改字段关系；它只是协议验收，术语、保护结构、blocking 与语义问题仍在整轮边界验收。通过协议的候选不算语义通过。
- API 六维/四维 checks 可以使用每维 [status,evidence]，两项均由模型真实填写；程序仅恢复字段名，共同持久协议仍为 status/evidence 对象。非空原文的 accuracy/completeness/logic 只允许 pass/corrected，不能自动把 not_applicable 改成通过。模板明确保留 keep:true 与完整 translation 两条路径，issues 只列当前未解决问题，已修正项记 changes/checks。
- 明确引用完整原文时可用 source_ref:"whole"（顶层和 changes 中均可），程序从本 session 绑定的完整原文恢复 source_excerpt。API 模板直接示范此路径，不再同时示范空摘录；模型明确选择 whole 时附带的空字符串摘录可忽略。非空摘录与 whole 冲突、未知引用仍拒收；局部引用仍须模型给连续原文。Codex 收集器也支持同一便利引用。
- 新增语境例外仍用本条 t编号；撤销已有例外改用本条 existing_exceptions 的 x编号，即使该术语当前不命中也可具证撤销。x编号绑定独立的 record_exceptions，不允许跨记录、不允许拿撤销引用新增例外。历史 t编号撤销与完整 source 协议继续恢复，不改冻结表。
- API 对可解析的正文回复先严格校验整包任务/范围/未知或重复 ID，再逐条转换；字段转换失败保留原条目和程序诊断，有效条目通过共用 batch_items 保存，缺失/失败条目进入共用 batch-repair。保留实际作者、付费历史和尝试次数，不自动补 keep、译文或证据。
- 单个整体格式重试耗尽后，API 槽位继续处理本轮其他独立待办，轮末统一交回异常；不在异常未处理时启动下一语义修复轮。不可重试的认证/服务故障仍暂停。局部可靠性修复在已重试或明确输出截断时缩小失败范围的分包上限；分包仍算同一次修复、不刷新累计预算，其他情况按正常预算分包。
- API 与批次结果解析均拒绝重复 JSON 字段和非标准 NaN/Infinity；允许移除已知代码围栏及字符串外的尾逗号，尾逗号编辑只在严格解析成功后写哈希/偏移审计。缺括号、引号、缺值等不猜修，不丢弃未知或重复 ID。原回复保持。
- 分配时同时检查完整 Codex 输入和实际 API 展示输入预算；短编号、例外视图及提示增加均计入，不截断原文或证据。
- 术语匹配沿用原有冻结表、最长匹配、词边界和保护区间规则；进程内按冻结对象及大小写/词边界选项准备一次正常/保留原文条目的匹配计划，减少逐句重复分类；不增加持久通过表。

## 2026-10-21：保留思考的无损输入压缩与耗尽停止

- 后续恢复验证：可选api.stream=true使用SDK流式接收，stream_options.include_usage=true；只拼接content，推理不混入正文，usage取最后累计值而非逐chunk相加，缺失用量仍未知。必须收到终止chunk，断流或缺终止时拒收，即使现有片段恰为合法JSON。默认保持非流式。旧项目API配置可使用新增可选字段，无需改写已绑定任务。HTTP200 HTML页面按不可自动重试的unexpected_html_response报告，不再当空回复重复调用。
- 公共Python入口Workflow.split_extract_recovery(task_id,record_limit=12,worker_stopped=True)：仅用于已暂停、明确旧worker停止且无活跃runner/reserved调用的未完成API提取。按完整记录分包，原文/元数据/出处不截断；继承父任务已用attempt、保存extract_split_lineage和父批次旧交接，成功批次不可拆。须有明确剩余预算；不重置历史。glossary_review准备只合并非obsolete的完成提取，并严格检查全部源ID恰覆盖一次，父批次过期不绕过覆盖门槛。短批真实生产验证66/66接收、0次失败；不代表术语已完成语义审校。

- 新生成的 API 展示包将重复的长 metadata 字段集中在 shared_metadata，以每条 metadata_refs 明确关联；资源地址重复的文件前缀放 shared_metadata_text，resource.prefix_ref 与 suffix 原样拼接还原。未出现字段不继承，独有值保留，既有命名空间不覆盖。原文、ordinal、保护映射、语义证据和未知上下文字段不删，canonical 任务及已有交接不改写；Codex 输入仍用完整数据。完整输入与输出预算检查不变。
- 这项改动不关闭思考、不改变模型和接口，也不把思考 Token 全部视为无效消耗。提取提示明确 source 为所引原文的连续片段并保留标点，不限制必要出处数量，不因节省而省略歧义。
- 无正文且 length 结束并报告 reasoning 时，返回不可自动重试的 reasoning_budget_exhausted；有正文但结果拒收且 length 结束时，保留回复/usage/拒收原因，暂停为 rejected_length_terminated_response。完整有效结果仍正常接收。恢复须检查批量和输出预算，不能关闭质量检查、接受截断、重置次数或原样盲重试；其他错误仍沿用有限重试。
- 本次游戏100批本地对照输入807,835→625,187 Token（减少22.61%），逐字段还原一致；这是本地估算，不能据此宣称全部服务端Token同幅降低。小样本真实链路验证遇401认证失败后停止，无生产状态改动；真实语义效果尚待接口恢复验证。

## 审校效率改进（隔离开发版）

本版不自动迁移已有游戏。新快照、新任务采用以下协议；旧冻结表没有 applicability 时保持原匹配，旧任务没有 layout_policy 时仍严格按原规则验收。

新增快照另带 `nontranslate_scope_policy=1`：禁翻词语/标识符必须显式给出已有的 `applicability` 和理由，不靠固定普通词黑名单。完整命令可选 whole_record，局部用法选 literal_context；全局 text 仍允许按键、品牌等有依据的保留，但独立、嵌入两种用例都随包提供时，现有四维审校必须各引用至少一条。占位符/标点保护不因此变成普通词翻译；旧表不自动升级。

`glossary_corpus_evidence.py` 用全文索引为词语型 non_translate 补充独立/嵌入的结构对照，仍最多两条完整补充原文，优先未展示的另一类用法；不要求定义提示词、不按 CSV 邻接推断语义，不新增模型阶段或回复字段。

`translation_workflow.py --project <项目> audit-mixed` 只生成 `TranslateFiles/reports/mixed_language_audit.json`，不发请求、不修改译文；常规 review/final_review 准备时及正式 export 也更新该报告。中文中的拉丁词、命中的禁翻规则均列为候选，匹配禁翻表不构成语义豁免；按键、品牌、人名等合理原文不因扫描被自动判错。核对仍在现有六维独立审校完成。

公开 revise-glossary 会记录作用域变化后已不再命中的旧语境例外，绑定当前词典哈希及原文/译文内容。草稿检查使用派生视图过滤这些失效引用，不改原始回复、候选日志和累计次数；其他例外或无法证明的情况继续严格检查。内容改变或术语重新命中时不沿用清理证明；受影响译文仍须独立语义审校，不因清理元数据而自动通过。

- 术语提取不把普通代词、助动词、常用词或裸技术字母当作全局术语。新快照带 applicability_policy=1；高歧义词在审校交接时必须隔离或补有证据的适用范围，而非等到全表冻结后才失败。风险词只是送审提示，程序不代替语义判断。
- applicability 为 {mode, case_sensitive, reason}；mode=text 用于正文专名/术语，whole_record 只匹配完整记录（忽略首尾空白），literal_context 须另给实际紧邻 prefix/suffix，至少一项非空。大小写逐词控制，不改全项目大小写开关；范围修订需要 evidence_rows。技术标记优先交由明确的保护规则，不因 HTML 的 i 而全局禁翻 I。
- API 审校可用记录内 evidence={e1:实际依据,...}；reason、六维 checks 的 evidence、changes.reason 可使用 {ref:e1}。各维状态仍须模型逐项填写，程序只复制同条实际证据。未知/空/嵌套/未使用引用拒收，不跨条继承，也不自动生成 pass。完整结果仍保存原有六维结构，原始回复保留。SubAgent 的共用收集入口也支持同条引用。
- 新项目默认 layout_normalization=exact_v1；新正文/审校/返修任务绑定 layout_policy。程序仅恢复段落数不变时的换行编码、首尾空格和制表符，按源文/已校验源文哈希约束处理并保存前后哈希审计。不给空译文补正文，不处理缺失标签，不插入或重排段落，不改变锁定项，不覆盖模型明确的格式修订，不清除模型提出的改动理由。语义状态不由程序填写，整轮检查和最终导出门槛继续执行。

测试与真实小样本见隔离工作区 Development/review_efficiency_live_20261023；不把局部协议节省量当作整项目费用下降比例。


## 术语范围与遵守等级（2026-10-24）

新提取合并标记 `terminology_policy=1`。保留的characters/terms条目由术语语义审校确认 `term_kind`、`enforcement`、`term_reason`，缺失时不能冻结：

- `proper_name`：人名、地名、组织、法术等确定专名，`enforcement=required`。
- `game_term`：具有原文证据的游戏特定义。确定的专用称谓为required；多义或暂定、只能作参考的译名为reference，并注明applicability。reference不强制字面命中，不为缺少参考译名产生修复任务，也不要求额外例外。
- 通常语义的user、human等普通称谓、常用词不提取；已混入的应quarantine，不能降为reference兜底。常见歧义词确属游戏属性/专名时必须提供whole_record或literal_context范围及出处，不能全局套用。程序仅能验证分类与范围契约及已知高风险词，不能替代模型判断所有词的常见程度。

提取和审校沿用原阶段、批量及四维语义证据，不新增一轮模型任务。API与SubAgent共用等级；任务原样传递字段。普通词是否被误标成专名仍须语义审校，结构通过不代表语义正确。

旧冻结快照未标级时保持既有required语义，不自动修改旧项目、已有审校结论或在途输入。需要治理已有词表时必须显式revise-glossary，绑定证据并按实际受影响条目复查，不直接改库。参考条目不能遮挡嵌套的必须遵守专名。

独立审校accuracy核对修饰对象、身体部位、动作参与者和物理/比喻词义；tone核对中文搭配，不能把术语命中当作准确性依据。例如stubbled scalp应结合scalp判断为短发茬，而不是胡茬。使用现有六维证据，不新增冗余报告或承诺杜绝全部语义错误。

结构检查新增对已知不显示的{Male}/{Female}标记新增独占括号的拦截；源文已有括号可保持/本地化。普通可见数值变量{0}不推定为隐藏标记，不作全局禁止括号。其他引擎标记需有实际证据后扩展，不删除标记或凭猜测恢复空值。


返修候选与keep的边界：新API包在原译与未接收candidate.translation不同的条目上绑定require_explicit_translation。该条必须返回完整translation，不能使用keep:true；否则程序拒收。keep只代表保留原译，不表示采用尚未接收候选。此约束不回写旧交接映射，不自动修改模型回复、源文摘录或语义证据；已接受的旧结果也不悄悄改写。开发实测曾出现模型证据解释正确候选却用keep恢复错误原译，因此加入此门槛及真实回归夹具。
