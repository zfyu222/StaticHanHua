# 固定术语审校步骤

每个游戏在术语提取完成后、正文翻译前执行。共享工具只负责机械检查、保存决定和安全应用，不调用 API，也不决定语义译名。原文或分析表重新生成后再次检查；历史译文不会自动改写。

## 快照与自动检查

新快照带 `nontranslate_scope_policy=1`。non_translate 中的词语/标识符须显式给出原有 applicability 与具体依据；不能仅因出现在 EXE 或命令样式列表就全局保护。完整命令通常用 whole_record，限定语境用 literal_context；按键、品牌等可用有依据的 text。符号、格式变量和括号运行标记不因此转成普通词。

程序从全文索引检索独立/嵌入两类字面用法，最多补两条完整原文，优先补尚未展示的另一类；这只是结构对照，不认定哪一处是命令或普通词。全局 text 的词语型禁翻项在两类用例均随包提供时，现有四维 entry_review 必须分别引用至少一条。未明确范围不能冻结，承认 audit finding 不等于已修正。沿用已有四维/edits 协议，不另加审校阶段或模型回复字段；旧快照不自动升级。

SubAgent 与 API 使用相同的术语提取、审校和冻结程序。正式输入为项目根目录无表头、单列 UTF-8 CSV，出处按 CSV 逻辑记录编号。先由共用流程完成 `extract`，再 `prepare --stage glossary_review` 生成审校任务；交接方式见 [共用批次](batch_execution.md)。

程序将提取结果转换为 `characters`、`terms`、`non_translate` 分析表，调用 `glossary_tool.audit/build` 保存快照、自动报告、原文出处、审校决定、审校版及 manifest。产物统一在 `TranslateFiles/glossary/`；不读取或写入 GUI 缓存。保留既有快照和轮次记录，不手工覆盖在途任务。

自动报告筛查空译名、同源重复、来源命中、分类和共用译名等问题；自动检查不能代替语义审校。需要更多上下文时核对正式原文及资源映射，不把 CSV 相邻记录当作剧情顺序。

## 人工审校（不能省略）

自定义流程在生成新的术语审校快照前，按英文原词（忽略大小写及首尾空白，不归并单复数或近义词）跨提取批次和分类合并，每个原词只占一个审校名额。所有出处去重汇总；不同译名、分类、备注及各自出处保存在 extraction_candidates。暂定译名按提取提案次数最多者选择，同票取首次出现者，分类沿用对应候选；这是程序选出的暂定值，不是语义通过。审校者一次比较候选并确认或修正标准译名，确属多义时记录语境区别。输入包提供每个候选至多两条代表性原文，全部出处编号仍保留；预算不足时拆分其他术语，不能将同一原词拆给多个审校实例，单项超限由分配程序报错。

已生成的旧审校快照、在途任务及已完成结果不自动改写；新规则用于后续新快照。历史 GUI 缓存不自动迁移或重写。

### 从全文取得定义，避免短标签丢失语境

新快照在绑定审校哈希之前，通过`glossary_corpus_evidence.py`本地索引全部源文，补充`corpus_evidence_rows`。当前针对英文的descriptor/ability/skill/spell/trait/class及定义提示词，优先选择明确“原词 + 类别”的记录；可再沿该类别的字面关系找一条解释类别的原文，总计每词最多补两条。例：`Hardened` → `the Hardened descriptor` → `Your Descriptor provides ... starting Stats and Skills`。程序不凭此自动认定为人物特质或决定译名；同形异义仍由模型核对。

原始提取出处继续保留；被记录但未作为代表出处展示的定义也能补入。补充出处是完整记录，使用原有预算与拆批，不附CSV相邻记录、不截断、不新增AI阶段；单项超限照常报错。`corpus_evidence_report.json`记录检索数量。只检索已提取的文本，英文提示词未覆盖的定义或缺少源文仍可能遗漏。

术语四维审校可引用本条的`evidence_rows`或`corpus_evidence_rows`，且对应原文必须实际随包提供；其他条目、不可见出处、改写摘录仍拒收。API和SubAgent使用相同完整出处，API仅隐藏未提供原文的编号。审校需说明实际修饰对象、物理/比喻词义及自然译法，不能以“确属机制名称”代替译名准确性；将确认的含义和适用范围写进已有`term_reason`，由原有术语输入传给下游。

若完整短标签与已审词条同名，但该词条的literal_context范围仅命中说明句，正文包仍以`label_meaning_hints`传递已有含义，避免标签独立成批时丢失语境。该提示不包含强制译名，不扩大术语适用范围、不触发术语返修或例外；严格按完整字串与大小写设置筛选，不给普通长句套用标签含义。没有已审含义的旧词条不自动推断。

常见歧义词确为游戏义时，只允许whole_record或有真实紧邻字串的literal_context；仅设置text+case_sensitive仍是全局匹配。拒收诊断明确原词、错误模式及合法选项，避免模型在不清楚门槛时反复提交。既有快照、在途映射和已完成结论不自动增补或改写。

检查全表来源、建议译名和分类，核对重要/可疑条目的原文。特别检查物品/书名是否被人物合并、复合名称截断、称谓遗漏、同一专名多译、技能/种族与普通词混淆、提示词示例混入、占位符误收。专业词根据游戏语境及必要的权威资料核对，不生搬现实含义。不把无匹配全判为虚构，也不把共用译名全判为错误。译名依据不足的条目隔离；隔离不是禁翻。

在报告中写明人工核对范围和未解决问题。完成语义审校后才将 decisions 中 `review_complete` 设为 true，填写 `review_summary`。`acknowledged_findings` 必须列出自动报告的每个 finding ID，表示逐项处理或核对认可。结构错误即使确认过仍会阻止构建。

决定格式示例（保留工具生成的两个哈希；索引从 0 开始，evidence_rows 为从 1 开始的原文记录号）：

```json
{
  "review_complete": true,
  "review_summary": "全表检查；具体语境核对范围及待确认问题",
  "snapshot_sha256": "工具生成值",
  "source_sha256": "工具生成值",
  "acknowledged_findings": ["F0000"],
  "edits": [{
    "table": "terms", "index": 0,
    "patch": {"recommended_translation": "经语境确认的译名", "category_path": "物品"},
    "reason": "具体修正依据", "evidence_rows": [12]
  }],
  "quarantine": [{"table": "characters", "index": 1, "reason": "依据不足，暂不启用"}],
  "additions": [{
    "table": "terms",
    "row": {"source": "实际原文", "recommended_translation": "译名", "category_path": "物品", "note": "审校说明"},
    "reason": "补充或移类依据", "evidence_rows": [12]
  }],
  "allow_no_match": [{"source": "确需保留的拼写变体", "reason": "对应关系和证据"}]
}
```

示例不能原样使用，删除不适用的项。edits 更新字段，改 source/译名必须有 evidence_rows；移类或拆分用 quarantine 原条目加 additions，新条目也必须有原文依据。所有索引指向原快照，不随删除改变。gender 支持男性/女性/其他；category_path 支持身份/物品/组织/地名/技能/种族/其他。其他背景分类写入 note，不扩展共用分析表枚举。

## 收集、构建与冻结

审校者按本批程序生成的模板提供真实四维证据和决定；API 的短编号由对应 session 还原，不能自行填写任务哈希。上面的完整决定结构说明持久化字段，实际交接使用生成的执行器模板。

全部审校任务完成后，通过 `translation_workflow.py --project <项目> freeze-glossary` 合并决定、调用共享构建检查并冻结术语。生成审校版、修改/隔离清单及 manifest；未解决的无命中项必须修正、隔离或填写有依据的 allow_no_match。检查或真实语义审校未完成时，不得开放正文翻译。

README 记录术语版本、数量、审校范围、隔离问题及冻结结果；提交小型审校产物和文档。缓存、备份和凭据不提交。后续术语修订通过共用程序执行，保留历史并按受影响范围复查，不能直接改冻结表。

## 新快照的术语适用范围

新提取合并快照标记 applicability_policy=1。普通代词/助动词/常用词和单字母候选必须结合出处审校：确为普通词则隔离，确为专名、属性或技术义则提供 applicability。text/whole_record/literal_context 分别表示正文匹配、完整记录标签、指定紧邻字串；case_sensitive 为逐词布尔设置，reason 为具体来源依据。literal_context 需实际 prefix/suffix，不能编造语法。edits.patch.applicability 仍需 evidence_rows，提交时检查而非拖到冻结。旧快照不加标记、不自动修表。


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
