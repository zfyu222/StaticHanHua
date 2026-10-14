# 自定义流程的质量门槛

适用于 Codex 子代理及自定义 API 路线。Sol 主对话管理流程和审查产出，子代理/API 产生术语、正文及语义审校结果。程序检查只能证明字段、来源引用和状态满足协议，不能证明语义判断正确；Sol 必须实际阅读证据。

## 独立实例和审校证据

Codex 的 `claim --worker` 使用 spawn_agent 返回的实际 agent ID。每批新建 fork_turns=none 的实例，先创建等待任务的实例，再领取、生成提示词并发送任务；默认验收后结束，下一批换新实例，不把全文划成三个长期对话。不使用 luna-1 等别名，不通过改名或新租约规避独立审校或工作量限制。程序在 `quality_attempts` 和 `quality_authors` 记录身份，拒绝对应产出实例领取或提交审校。提取者不得审自己的术语；翻译者不得审自己的正文；复审者也不得是对应润色者。不同实例可以使用同一个 Luna 模型；不需要把全文语义审校转给 Sol。工作量、局部修复和完整输入预算见 [自定义流程](custom_workflow.md) 的上下文生命周期节。API 每次请求使用新的身份与独立上下文，不携带翻译者的推理或聊天历史。

全文正文审校逐条返回六维 checks：accuracy（含义与主体）、completeness（漏译增译）、terminology、tone、logic（否定/条件/数字/关系）、placeholders。各项含 status=pass/corrected/not_applicable 和具体 evidence。试译的精简协议见下节。非空文本的含义、完整性、逻辑不能标为不适用。source_excerpt 必须是该条原文的连续摘录；reason 解释核对依据。空原文可使用空摘录，并说明检查空译文。

修改译文时同时提供 changes：category（上述维度）、severity=minor/major/critical、source_excerpt、before（任务内完整原译文）、after（完整修订译文）、reason；checks 中须标出 corrected 项。未改时 changes=[]。不要机械复写“符合原意”；只在不涉及某维度时说明其不适用原因。关键疑难项为 blocking，不能伪造通过。

术语 decisions.entry_reviews 逐条覆盖分配 ref，包含 evidence_rows、source_excerpt、reason 和 source/translation/category/consistency 四维 checks。语境引用必须来自任务提供的出处。全表 summary 不替代逐项语义检查。冻结决定保留每项证据与实际 review_instances；术语修订也必须提供全表 entry_reviews、review_instances（ref→实际审校实例 ID），经独立审校后由主代理调用 revise-glossary。

## 代表性试译

术语冻结后先执行 pilot_translate、pilot_review，不能直接 prepare translate。默认抽 150 条，不足时全部；按类型、风险与长度兼顾覆盖，不追求全文或每个场景。试译是校准文体和规则，不复制一套昂贵的全文审校过程。每批最多 40 条，150 条默认分成 40/40/40/30，翻译和独立审校各 4 批；pilot_batch_count=1 请求尽量少分批，小样本可单批，不会按场景或角色拆成几十批。完整输入预算仍检查。

默认累计上限 pilot_call_limit=12：翻译、审校、恢复重领和重试合计最多 12 次执行领取。150 条首轮通常需 8 次，余下 4 次供定向修复；上限不因配置/文体变化或任务淘汰而刷新，旧项目仍沿用保存的上限，若改为 12 也保留已用次数。旧运行的历史消费单独保留，从首次启用预算政策开始计数。该计数是任务执行次数，不是底层模型消息数；一次执行内禁止轮询式聊天和长时间反复工具调用。派发前调用 pilot-plan，预算或输入检查不通过就停止创建实例；不要让额外 Sol 子代理调度或监视试译，程序负责等待和机械检查，主 Sol 只做最终验收及必要问题判断。

pilot_review 优先使用精简 JSON：顶层 review_summary 一次说明六维核对，各项含 status、evidence。审校者仍逐条对照全部分配 ID，无问题记录仅返回 `{id, reviewed:true, verdict:pass}`，不重复输出译文、原文摘录和六段理由。修改或疑难记录返回 verdict:corrected、完整译文、reason、source_excerpt、六维 checks、changes、issues 和必要 exceptions；仍按原协议检查占位符、术语、锁定状态和 blocking。批总结不能替代逐条检查，缺失 ID 会拒绝提交。Sol 报告中同一批总结只出现一次。

既有项目保持已选样本及完成结果，不因升级重跑。需要重新合批时先暂停并结束/回收在途任务，再通过 configure 调整 pilot_batch_count=1、pilot_call_limit=12，prepare 仅准备未完成语句。用户希望进一步降低样本量时，可用 `reduce-pilot --records 30 --reason <降低校准成本>` 复用已译样本；不要在有在途实例时改样本。

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

## Sol 风险审查与跨批一致性

全文审校以收敛明确错误为目标。首轮独立子代理完整对照每条原译文，保留六维证据；含义准确、自然可读且符合冻结术语和既定文体即可通过。同义替换、个人偏好和另一种正确译法不列为错误或 warning，不为此返工。polish 默认关闭，不因正文完成而自动启动全文润色。

首轮问题按明确 ID、原文依据、错误和验收要求集中整理；同一原任务内的问题合并为一个 --ids 修复任务，避免每发现一条就往返一次。不同原任务保留各自来源，不手工拼接任务或覆盖 DB。Sol 先用 checkpoint-quality --kind final 保存已经实际检查通过的项，再定向退回问题 ID。修复后的子代理只修复指定问题并确认修复未引入错误，不顺带重写无关措辞；重新生成的 Sol 报告只读取 records_to_check、consistency、glossary_checks 中仍待查的内容，不重新阅读全部译文、旧报告或全部 reused_decisions。

默认 max_attempts=3 是每个任务首轮加至多两次重新领取/修复，包含失败恢复，局部修复继承原任务的尝试次数；不能新建别名任务或改版本以刷新额度。达到上限仍未解决时由 Sol 判断真实语境、工具/规则缺陷及明确下一步，只继续必要范围，不能为了继续运行自动开新一轮全量审校。已通过项不反复退回；可选风格优化不作为交付阻塞项。程序仍在全库运行占位符、术语和对齐检查，这些机械检查不需要模型重读全文。

所有正文完成必要语义审校（包括启用润色时的 final_review）后：

```powershell
& $python $flow --project $project quality-report --kind final
```

final_quality_review.json 含数字、否定、条件、人物关系、变量、长句、关键交互、缺少上下文的短句，以及审校修改、warning 和术语例外的记录。检测是启发式候选，不保证覆盖所有语言或所有语义风险；同时抽取最多 20 条普通记录给 Sol 检查漏检。源文与逐条审校证据一并提供。

跨批检查汇总同一原文的不同译文，以及按说话人分组的口吻样例（每组最多 12 条，含全部记录 ID）。Sol 判断差异是否由语境导致，核对称谓、语气、任务条件和相同 UI 提示的表达。缺少 speaker 元数据时不能声称完成角色口吻检查；需要更大范围时将所需上下文交给独立子代理复核，产出证据后由 Sol 验收。程序发现差异不自动统一，Sol 也不代写译文。

发现问题时写明错误与验收要求，用 reopen-body --ids 退回已完成的 pilot_review/review/final_review 中有问题的语句。程序创建只含这些 ID 的修复任务，保留原任务及未受影响语句的审校证据；不传 --ids 才是整批退回。对修复任务继续退回时使用返回的新 task_id，修复继承原任务的尝试次数，不刷新预算。试译修订优先使用明确的 revision_request 和完成后的文体总结，不为了局部措辞问题改全局配置、重译全部样本；默认累计上限 12 次（首轮通常 8 次，余下用于定向修复），旧项目沿用保存的上限，耗尽后停止自动执行。程序保留锁定译文，淘汰受影响的后续任务并清除旧验收。术语修改按命中语句扩大复查范围；语言、文体或全局提示词等语义规则修改时，仍需检查其影响范围，不能直接沿用全部旧证据。

```powershell
& $python $flow --project $project reopen-body --task <任务ID> --ids <语句ID1> <语句ID2> --reason <具体问题>
# 领取、修订、提交，并完成受影响的后续阶段
& $python $flow --project $project quality-report --kind final
& $python $flow --project $project approve-quality --kind final --decisions <Sol实际审查决定JSON>
& $python $flow --project $project export
```

报告和决定绑定当前原文、术语、译文、审校证据和文体。译文或审校结果变化后旧报告不能再次验收，旧验收不能用于导出。导出报告明确记录 in_game_check=pending_repack，不将文本验收误称为实机通过。

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
