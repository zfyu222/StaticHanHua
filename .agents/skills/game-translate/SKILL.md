---
name: game-translate
description: |
  Manage the translation stage of StaticHanHua game localization. Preserve the AINiee workflow and offer a custom file-driven workflow with Codex subagents or API execution. Use for translation preparation, terminology extraction and contextual review, translation, proofreading, polishing, placeholder validation, and recovery. Do not translate a game merely because the workflow is being developed.
---

# Game Translate（翻译阶段）

## 入口与授权

先读项目 README.md 和已保存的 TranslateFiles/config/workflow.json。未指定工作流时让用户选择 **AINiee** 或 **自定义工作流**；自定义再选择 **Codex 子代理** 或 **API 程序**。已有明确选择直接沿用，不重复询问。选择尚未确定时可继续源文分析，不运行正文翻译或付费请求。

- AINiee：保留已有工作流和本地改进，读取 [AINiee 流程](references/ainiee_workflow.md)。默认生成准备文件，由用户在 GUI 翻译；用户明确要求 AI 直接翻译时按授权执行。
- 自定义：用户要求采用自定义流程执行汉化即授权其内部自动处理，读取 [自定义流程及调度](references/custom_workflow.md)，调用 Tools/localization/translation_workflow.py。Codex 方式按参考规则用子代理并行任务；API 方式由程序请求用户已选服务。未指定模型时当前采用 Luna 子代理，后续用户选择优先。
- 仅术语审校：按 [术语审校规则](references/glossary_review.md)；不要因此启动全文翻译。
- 用户提供译文仅要求校验：直接执行校验，不必询问工作流或改动原项目后端。

## 共同边界

总体仍是一个翻译阶段，内部子阶段不要铺到基础 AGENTS.md 或项目概览。

- 正式输入：Projects/{GameName}/{gamename}_text.csv；正式输出：同目录 {gamename}_text_translated.csv。无表头、单列，按 CSV 逻辑记录逐条对应，逗号/换行合规转义；不能用物理行或模型输出顺序对齐。
- 所有翻译中间产物位于项目 TranslateFiles/，包括 TranslateDoc、配置、术语审校、任务、结果、报告、缓存及备份。绝不写入 Game/ 链接。既有项目不自动批量搬迁。
- 正文前完成全文术语提取、整理、程序检查和语义审校，再冻结使用；无术语亦记录检查。程序通过不等于语义审校完成。
- 自定义正文、审校和润色任务由程序筛选相关术语；子代理不读取完整表，不新增或自行修改冻结表。
- 不附带 CSV 相邻记录；提取顺序不代表剧情顺序。一致性依靠冻结术语、文体和已确认的角色/场景元数据；术语审校仍保留条目明确引用的原文出处。
- Codex 子代理默认一批一个新实例（fork_turns=none），验收后释放并创建下一批实例；三个并发槽位不对应三个长期对话。必要信息由任务文件传递，不携带上一批聊天。上下文预算与同批修复规则见自定义流程。
- 自定义 Codex 路线中，主对话（Sol）只负责主流程调度、质量检查及产出物审查。术语提取、逐项语义审校、正文翻译、润色及译文修订由子代理执行；主代理发现问题后退回相关任务，不自行代写批次结果。具体分工见自定义流程参考。
- 自定义路线先做代表性试译并经 Sol 验收，再开放全文翻译；审校实例必须与对应产出实例不同，审校需逐项留下结构化证据。导出前经 Sol 审查高风险文本、普通抽样和跨批一致性；实机检查在回填后记录，未通过不得发布。具体协议及门槛见 [质量检查](references/quality_workflow.md)。
- 试译是低成本校准：默认 30 条、翻译和独立审校各一批；领取及重试总计最多 6 次，修改配置不刷新预算。派发前检查 pilot-plan，预算不足时停止，不再创建子代理；不创建额外 Sol 调度子代理。既有大样本用 reduce-pilot 优先复用已译样本，不能为了升级流程清空重跑。
- 占位符、字面转义、标签与变量严格保护；源文中的玩家选项不误当机器变量。细则见 [保护边界](references/placeholder_patterns.md)。
- 自定义使用 ID JSON 协议；AINiee 才使用原 textarea 提示词要求。两种协议不能混用。
- AI 可在执行中检查和调整；通过程序暂停、修订和增量复查，不能直接改 DB、覆盖在途版本或忽略锁定译文。按配置限制重试，关键未解决问题不能标记通过。
- 缓存/备份/运行状态及凭据不得提交。Game/ 永不提交。

## 验证与交接

AINiee 译后使用 scripts/validate_translation.py，显式指定 --src-col 0 --dst-col 0；报告与备份用 --report-dir 和 --backup-dir 指向 TranslateFiles。退出码 0 才能交接。自定义必须由程序 export 完成其全部质量门槛，不能手工拼接最终 CSV 绕过验收。

更新 README 的翻译节与技术栈，记录方式/后端/模型、术语版本和审校范围、占位符、验证及待确认问题；按项目规范提交 README、正式译文和 TranslateFiles 下适合入库的配置/审校/交付材料。仅准备阶段用“文档”提交，翻译通过用“翻译”提交。
