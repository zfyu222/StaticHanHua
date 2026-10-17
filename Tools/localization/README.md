# 翻译流程工具

自定义工作流的程序入口是 `translation_workflow.py`。原 AINiee GUI 工作流保留；入口选择及子代理派发由 `game-translate` 技能管理。所有翻译中间产物放在游戏汉化项目 `TranslateFiles/`，正式原文/译文保持项目根目录的单列 CSV 格式。

## 运行

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
  "batch_records": 80,
  "review_batch_records": 80,
  "review_output_tokens": 16384,
  "batch_chars": 12000,
  "context_records": 0,
  "polish": false
}
```

已有配置恢复时不重新 init。`--context` 可读取提取时的上下文映射：JSON 对象按源文逻辑记录号索引，值可包含 group、speaker、resource、scene、text_type、note；各字段为字符串。术语提取按 group 分批；正文翻译与审校可跨 group 合批，每条保留已核实元数据。speaker 可让程序补充相关人物术语。context_records 仅为兼容字段，即使旧值为 2 也不附带相邻 CSV 记录。

术语语义审校默认每批最多 60 个条目（glossary_review_batch_records=60），同时受 batch_records 和 task_input_chars 限制。既有配置保留显式旧值；调整时使用 pause/configure/resume，不直接修改运行配置或在途任务。

词族候选由 glossary_families.py 在本地生成：识别英文书写/部分单复数变体、保守派生词根及完整词组件，尽量同族同批，保存 glossary/r{revision}/family_candidates.json。不新增模型请求阶段，不自动改译名，也不回扫全文补提取。每批 term_families 最多 120 条关系提示、12 个跨批精简关联词（不重复附带其原文出处）；原有自动 finding 关联证据保留。审校在既有 consistency 证据中判断核心译名统一及语境差异，无须额外词族报告。候选可能误报或漏报，非同义结论，非通用词形分析器；已有任务及冻结表不自动变更。

草稿扫描支持 `draft-scan --workers 4`，本次调用临时使用 4 个校验线程；也可在配置补丁中设置 `"draft_scan_workers": 4`，通过现有 configure 流程持久保存。线程数范围为 1～32，默认 1；短文本正则匹配实测可能因线程调度变慢，应按项目测试后选择。扫描复用已读取的记录快照，每条记录只匹配一次术语。工作线程不访问 SQLite、不写文件，主线程按原文顺序汇总并保存结果；排队任务最多为实际线程数的两倍，线程异常会中止扫描并回滚数据库事务。

init 默认从项目 README 的游戏信息、简介、背景设定、世界观和文体等章节自动读取游戏背景，保存到 TranslateFiles/config/game_background.json。Luna 与 API 的每阶段任务都会携带这份快照、文体及相关上下文。可用 game_background 字符串替换为已核实的背景摘要；设为 null 则自动读取，设为空字符串则不载入背景。运行中更新 README 后需暂停并 configure 重新读取，不会自动改变在途任务。背景缺失时不推测剧情。

AINiee 选择为 `workflow=ainiee`；init 只生成准备文件，不调用翻译。新 GUI 项目导入 TranslateFiles/source 下的源文快照，保持“项目缓存随输入目录”开启，缓存即可留在 TranslateFiles 内。旧项目缓存不自动移动或覆盖，原始 GUI 入口及使用方式继续可用。

## 程序接口

| 命令 | 用途 |
|---|---|
| init / status | 保存选择及快照、占位符分析；查看进度与异常任务 |
| prepare --stage ... | 按阶段生成任务，重复调用不重复创建已覆盖任务 |
| claim --stage ... --worker ... | 事务领取任务、生成独立任务文件与租约 |
| prompt --task-file ... | 为已领取任务生成协议提示词文件 |
| submit --task ... --token ... --result ... | 按阶段协议保存结果；正文和独立审校候选需在整轮结束后统一验收 |
| batch-open / batch-finish / batch-repair | 正文任务单文件交接、集中保存；batch-repair 仅用于试译或保存可靠性问题 |
| batch-split --task ... | 拆分未尝试、无交接的首轮或修复审校任务，同时检查输入和输出预算 |
| pilot-plan / quality-report / approve-quality | 试译预算规划、质量报告及实际验收 |
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

阶段依次为 extract → glossary_review → freeze-glossary → pilot_translate → pilot_review → Sol 试译验收 → translate → draft-scan 及定向修复 → review → review-scan 及定向修复 →（可选 polish → final_review → review-scan）→ Sol 遗留争议闭环 → export。回填实测后再 record-game-check → release-check。每个阶段内部任务可并行，由 AI 读取相关任务、术语和上下文。正文子代理不负责新增术语。

Codex 子代理由主代理通过平台工具调用，普通 Python 不负责调用聊天内的 spawn_agent。API 执行复用 AINiee 的客户端工厂、平台思考参数和 SSE 解析；本版支持 OpenAI 兼容接口。原 AINiee 的其他原生接口仍可用，尚未接入新程序。

API 配置示例（仅示例，不会自动执行）：

```json
{
  "backend": "api",
  "api": {
    "target_platform": "deepseek",
    "api_url": "https://api.deepseek.com",
    "model_name": "填写用户选择的模型",
    "api_key_env": "SHH_TRANSLATION_API_KEY",
    "concurrency": 3,
    "requests_per_minute": 30,
    "max_requests": 100,
    "request_timeout": 120
  }
}
```

凭据只从对应环境变量读取。请求预算累计跨阶段，不因重启清零；达到预算暂停。SDK 隐式重试关闭，程序按可重试错误及 max_attempts 控制。没有选择 API 时不得自动切换。代码测试不使用真实 API 凭据。

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

## 审校输出预算

review_batch_records=80 是独立条数上限，审校不再按翻译条数直接合批。review_output_tokens 默认 16384；API 阶段取其与 api.max_completion_tokens（默认 8192）的较小值，再预留 20%。程序根据原译文、可能的完整前后修订、摘录和六维证据估计输出，按预算提前拆批。估算不是精确 tokenizer，不保证模型绝不截断；不得为满足预算截断原文或审校证据。

正文审校、复审及其修复采用相同预算；试译审校按精简协议单独估算，仍最多 40 条且总领取预算保持 12 次，pilot-plan 计入拆分后的审校批数。单条超限时在创建任务前明确报错，需调整输出额度或设计保留 ID 对齐的切分。完整输入和预计输出预算统一在分配阶段检查并自动拆批，claim/batch-open 领取时不再重算或校验字符/输出预算。prepare 会按同一规则重新分配尚未尝试的旧待办任务；可用 batch-split 显式拆分当前 revision 中尚未尝试、无交接的 pilot_review/review/final_review 任务（首轮和修复均支持），同时满足输入与输出预算，保留 ID、修复计数及证据。已领取的任务不能拆分。

## 完整输入在分配时拆批

workflow_allocation.py 是初始任务、草稿修复和独立审校修复的统一分配入口。先组成完整输入，再检查 task_input_chars；同时满足提示词 + JSON 和 Codex input.md 两种交接的长度限制，预留实例身份、租约及尝试数字段。超限递归拆分，并按拆后的任务重新筛选术语；术语审校同时重算自动 finding 必需的关联项、原文出处及有限词族提示。单项及必要证据超限则回滚本次分配并明确报错，不能截断证据。

预算检查仅发生在分配阶段，claim 和正常 batch-open 不再渲染、计算或拒绝超限输入；回执字符数直接读取 task_allocations 分配记录。版本、租约、独立身份、修复停止条件及执行次数检查仍按原协议执行。prepare 对当前修订中未尝试的旧待办任务补做分配，保留已完成、已尝试及在途任务。旧的已尝试任务若确需调整，先结束在途任务再按已有配置变更流程重新准备。试译按拆后的实际任务数检查剩余次数预算。


## 程序准备任务，子代理专注本职

所有阶段统一遵循：程序准备合格输入 → 子代理完成本阶段工作并写出结果 → 程序收集、合并、验收 → 程序生成下一轮任务。适用于术语提取、术语审校、试译、正文翻译、独立审校、润色、复审、所有定向修复、Sol 裁决与 API 路线。

程序在分配前负责：来源与修订绑定、ID/逻辑记录号一致性、原文完整性和保护映射、字段类型、已有译文/锁定约束、继承例外与修复计数、相关术语和必要出处筛选、完整输入及预计输出预算、自动拆批。坏输入在分配阶段报错，不交给子代理修包。新分配记录保存 input_contract_version=1；prepare 自动更新未尝试的旧待办任务，已完成和在途任务保留。

程序提供 output_template / result.template.json，预填任务 ID、版本、记录 ID、术语 ref 和必要字段骨架。语义状态仍是 false/null/pending，证据和理由留空；不得由程序自动填 pass、编造证据或代替语义审校。子代理只填本阶段的实际译文、术语决定、审校证据或疑难裁决。

主代理派发 input_file 和 result_file 即可。术语阶段 claim 后调用 prompt，同样返回包含任务材料及模板的单文件 input_file；prompt_file/task_file 仅保留兼容，不再把它们重复发送给实例。正文阶段继续 batch-open。task_ready=true 表示本次交接已由程序准备；false/旧合约应由主流程处理，不让子代理补做校验。

子代理不执行任务预算、版本、ID 集合、字段结构、占位符统计或 JSON 合规检查，不运行 validate/check/audit 脚本，不重分批、不扫描全库、不修任务文件/数据库。翻译实例只翻译；审校实例逐条做语义核对并填写证据；润色实例只作必要润色；Sol 实例只裁决或修复分配的难题。语义核对属于审校本职，不能因机械检查前置而省略。

发现文件缺失、字段损坏或源文不对应时，只简短报告 input_problem，由主流程定位输入生成问题。原文本身的歧义按本阶段 issues/语境例外处理。程序负责产出后的解析、ID 对齐、占位符/结构/术语校验、结果合并和整轮验收；实际执行次数及真实产出身份仍完整记账。租约、实际 worker 独立性和执行次数等运行状态由程序管理，不能要求子代理自行证明。

试译验收也由 quality-report --kind pilot 提前生成 inspection_manifest 和 next_input_file，包含按包筛选的批总结、文体背景、覆盖统计及输出模板；按包 checkpoint-quality 后重新获取下一包，最终完整覆盖仍由 approve-quality 判定。单个不可拆检查超过预算时在分配阶段报错。Sol 是验收/升级任务模型角色；主对话不是 Sol 时创建新的 Sol 子代理执行这些任务，不要求切换主对话模型。
