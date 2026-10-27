# API 一个阶段的自动多轮

用户明确要求舍弃旧正式正文并从头开始时，先暂停并停止旧执行器，再调用公共入口 `translation_workflow.py --project <项目> restart-body --reason <原因> --stopped-pids <已终止父/子/worker PID...>`。入口核实 PID、runner 锁及锁定文本，先在写事务外制作 SQLite 完整备份，再将全部正式记录置为 source、清理正式候选与检查缓存、作废旧正式任务，并建立新的正式修复预算 revision 范围。已冻结术语、已接受试译、付费请求、作者及 lineage 历史保留；reserved 请求记 interrupted_restart，用量保持未知。此入口保持暂停，不创建任务或发送 API。接着由主流程通过公共 resume 和官方 runner 启动新正式阶段；普通中断恢复仍使用 --resume，不使用 restart-body。

正式入口仍是 `run_api_round.py --project <项目> --stage <阶段>`。子进程的 `run-api` 默认连续处理一个阶段，Python `run_api()` 同样默认 continuous=True；`--single-round` / continuous=False 仅供单轮调试和底层测试。

## 影响范围

| 阶段 | 一次调用负责的范围 | 交回主流程的节点 |
|---|---|---|
| extract | 完成本阶段任务，原有格式/传输重试保持 | 术语整理及下一阶段 |
| glossary_review | 完成本阶段语义审校任务，原有格式重试保持 | 冻结、冲突或需要裁决的问题 |
| pilot_translate | 本轮试译并生成 pilot-report | 主 AI pilot-decide，不自动接受或试译重试 |
| translate | 全部正式草稿落盘，仅做交接结构校验及格式/传输重试 | drafts_complete=true、next_action=draft_check；主 AI 可检查或提交版本库 |
| draft_check | 首次 draft-scan → 必要的 API 修复 → 整轮复查，直到 clean | 主 AI 决定启动独立审校，或处理暂停/异常/Sol 升级 |
| review | 全轮候选和证据 → review-scan → 失败项新轮，直到 clean | 下一阶段，或暂停/异常/Sol 升级 |
| polish | 按原配置完成润色任务，不扩大范围 | 下一阶段，不自动启动最终审校 |
| final_review | 与 review 相同的整轮检查及失败项循环 | 主流程最终闭环，或异常/Sol 裁决 |

## 共用实现与停止

选路授权优先于恢复便利：配置启用优先级时，不传 `--api`，让程序按原优先级自动选择；只有用户明确点名接口才允许固定。不得因代理接口失败、格式错误、推理耗尽或认为官方更可靠而自行切官方。拆批、恢复和新阶段不能绕过此约定。本地已配置官方密钥不表示允许直接使用官方。不要擅自增加尝试、请求或 Token 上限来延长付费执行；先停止并说明原因，用户明确同意后才调整。用户要求停止费用消耗时终止相关进程，不自行恢复。

`workflow_api_cycle.py` 只组织已有单轮 API 执行、draft_scan、review_scan；不另写校验规则，不代填语义证据，不修改锁定项、累计尝试数或升级路由。每个请求仍独立输入、独立真实执行身份，无聊天历史；API 客户端复用不代表同一个语义审校实例。

正式翻译和程序审校使用两次独立调用：先 `run_api_round.py --project <项目> --stage translate`，返回主 AI，完成需要的检查或阶段性 Git 提交后，再调用 `--stage draft_check`。draft_check 是执行边界，不新增数据库任务阶段；修复仍复用原 translate 任务、预算和验收。没有问题时只扫描，不创建 API 客户端或发送请求；已有未完成修复轮先接续该轮，不重派通过项。`--resume` 同样恢复原 translate 修复任务已保存的回复。仅扫描及手工控制仍可使用 `draft-scan --prepare-repairs`。

必须先保存完本轮所有批次，才扫描整轮、生成下一轮。不因单批质量问题插队返修，已通过条目不重派。质量问题按原每 ID 常规最多 6 轮、回退提前升级等规则执行；格式与传输重试仍在单轮内部处理。循环另有无进展及保护上限，不能绕过原预算。

同一阶段内只创建一个Workflow，API客户端按配置的接口分别复用，普通多轮共享同一优先级管理器，持续复用未变化的冻结术语对象及内存命中缓存。阶段轮次边界发现API配置变化会明确停止，不改写在途请求。没有新增数据库缓存表。单独启动另一个阶段或异常重启仍需重建内存缓存，首轮未缓存文本的匹配耗时并未因此消失。

请求预算用尽、暂停、未知在途交接、失败任务、质量停止条件均返回 needs_attention。Sol/其他非 API 执行器任务不由普通 API 代执行；混合轮可先保存普通 API 批次，剩余外部任务交回主流程，整轮未结束前不检查或批准。恢复仍通过 --resume，保留已完成任务和累计次数。

不会自动从 translate 进入 draft_check，也不会从 draft_check 进入 review；不会自动启动润色、导出、回填或发布。试译整体校准、术语冲突及遗留争议仍由既有主 AI / Sol 流程处理。

## 日志

每次阶段调用生成自己的 profile；translate 与 draft_check 分别记录日志、耗时及本次 Token，项目累计 Token 连续计算。cycle_started/cycle_checked/cycle_stopped 记录循环，stage_boundary 标记正式草稿交回主 AI；api_cycles 保存各轮派发计数、检查范围、失败数及报告路径。draft_check 初始扫描发生在第一次 API 修复前，纯扫描可没有 API 轮次。阶段耗时、总请求和 Token 跨轮汇总，重试费用不漏记；嵌套计时不可相加当墙钟总用时。扫描中的进度文字写 stderr，stdout 保留单个最终 JSON。completed 只代表程序正常交回；translate 的 stage_clean=false 表示尚未程序审校，drafts_complete=true 仅表示草稿已收齐。stage_clean 仅在 draft_check/review/final_review 通过相应共同检查后为 true，不等同于项目整体可以发布。

用户明确要求升级修复继续用API时，可通过公共configure设置 escalation_backend=api（默认codex）。保留 repair_tier=sol 作为原升级预算层、累计次数、回退统计和停止条件；required_model置空，不冒充Sol，也不放宽独立审校和最终导出。新升级任务按该后端分配，旧在途任务先结束；通过公共配置修订使未执行旧任务过期再按当前诊断分配。此例外必须来自用户明确选择，并在项目记录。

API服务故障与模型结果失败分开计数：连接/超时、HTTP错误、空响应及流中断保留原始api_calls和已知/未知Token，但不计入本地max_requests额度与批次max_attempts；已返回模型结果但协议不合格、输出截断或推理预算耗尽仍计数。新请求绑定task_attempt，分包同一次尝试只在全部请求均属服务故障时返还；旧初始交接按session追溯，旧修复分包按token+round保守追溯。连接失败、超时、流中断在同一交接重试链最多额外3次，依次等待2/4/8秒，复用公共batch-retry并保留原始故障、累计尝试和结果；等待可被暂停打断。耗尽后停止派发并收回在途结果；403、401、429、其他HTTP错误和空响应等仍首次故障暂停，不循环重试。SDK重试保持关闭，避免重试次数叠加。并发严格读取当前API配置，不由执行参数或临时覆盖控制。

API 启动时为任务领取、历史交接、执行身份和计数查询建立幂等数据库索引，既有任务、结果、预算和质量证据不变。领取任务按原排序逐项检查剩余尝试和审校独立性，找到首个合格任务即停止读取，不重复载入所有待处理批次的大字段。索引准备耗时记录为 dispatch_index_setup；仍保留派发锁和事务锁，不能把配置的并发上限当作保证持续满载。

用户明确启用`api_failover`及`priority`时，采用每API独立的正常→暂时停用→待重试→等待结果状态机，详见`Tools/localization/README.md`。连续5次服务错误（成功清零）停用60秒，到期待重试仅放一个真实任务探测，等待期间其他任务跳过该API；成功恢复正常、失败重新冷却。取消2/4/8秒探测；每次派发/失败重试都重新按优先级扫描，所以高优先级恢复即可回切。全部不可派发则等待冷却/在途探测结果，可用公共pause中断；旧在途结果不能误更新新一轮状态，未发出的取消探测不能算成功。显式`--api`固定一家但保留已启用的冷却；未启用优先级维持上段兼容规则。每家并发/限速/思考读取配置，预算与质量门槛不重置；每次SDK调用单独记账，保留服务端Token和实际API/模型，未知用量保持未知。`logs/api_failover`与`reports/api_failover`记录四态变化、选路、连续失败和用量；返回率不是语义准确率，统计日志不含密钥和正文。

仅提高 review_output_tokens 时，暂停且无 reserved 请求、无运行中的非 API 任务，可由公共 configure（impact=none）原位更新输出容量；保留修订号、已分配批次原大小/输入哈希、交接、结果与累计预算，并记录配置历史。该例外不允许降低容量或夹带其他规则；API max_completion_tokens 仍须满足新容量。sync_api_batching 复用此入口，避免旧交接阻止用户提高输出预算后接续。

输入预算按实际执行器的完整发送包计数：API 使用 compact protocol 的实测 Token，Codex 使用其完整执行包；内部 canonical JSON 的审计计数保留，但不额外当作 API 的发送量。所有候选、出处、保护和语义证据仍完整保留。降低 task_input_tokens 时，暂停、无 runner 锁且无 reserved 请求，并确认全部未完成任务均为已测量且不超新上限的 API 包，公共 configure 可原位降低上限，保留任务哈希、修订号、租约、结果和尝试预算；任一包超限或未测量则拒绝，不以恢复为由放宽上限。
