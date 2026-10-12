> 所有新生成中间产物放项目 TranslateFiles/；原文和正式译文留根目录。旧缓存不自动搬移，正常退出、备份、核对引用后再迁移。

# AINiee 翻译准备与校验（保留工作流）

## Default behavior

默认只完成翻译准备，不直接翻译：

1. 分析源文本中的占位符、标签和字面转义；
2. 生成 AINiee 可导入的禁翻表、TranslateDoc.md 与术语建议；
3. 术语提取完成后必须进行来源、结构与语义审校，应用审校结果并验证；
4. 由用户在 AINiee GUI 中执行实际翻译；
5. 用户提供译文后，校验并修复可安全修复的格式问题。

不要因为用户说「开始翻译」或「翻译」就创建 {gamename}_text_translated.csv。只有用户明确要求「直接帮我翻译」「由你翻译」或同等意思时，才可直接翻译。

若源文本很少（建议不超过 50 条），可以在完成分析后询问：「本次只有 N 条，是否要我直接翻译，还是仅生成 AINiee 配置？」在用户作答前仍不得生成译文。

## Inputs and outputs

- 输入：Projects/{GameName}/{gamename}_text.csv
- 准备阶段产物：
  - Projects/{GameName}/TranslateFiles/TranslateDoc.md
  - Projects/{GameName}/TranslateFiles/placeholder_report.md
  - Projects/{GameName}/TranslateFiles/ainiee_exclusion_list.json
- 译后校验输入：Projects/{GameName}/{gamename}_text_translated.csv
- 校验失败时：Projects/{GameName}/TranslateFiles/need_retranslate.csv

源文件与译文必须一行对应一条文本；没有表头。文本本身含逗号或换行时，使用合规 CSV 转义，避免 CSV 解析拆列。

## Workflow

### 1. Read the project

阅读项目 README.md，确认原语言、目标语言、引擎、文本来源和已知的字体或术语限制。

新项目准备时先使用自定义流程程序的 init 命令（workflow=ainiee）保存选择并生成源文快照、报告与禁翻表；该命令不会启动实际翻译。GUI 导入 TranslateFiles/source/{gamename}_text.csv 快照，并开启“项目缓存随输入目录”，缓存自然保存于 TranslateFiles/source/AiNieeCache；先输出到 TranslateFiles，校验通过后复制成根目录正式译文。既有未完成项目保留原缓存，先核对并备份，再处理目录迁移，不能用空项目覆盖旧进度。

### 2. Analyze placeholders

运行：

    python "D:\StaticHanHua\.agents\skills\game-translate\scripts\analyze_placeholders.py" "Projects/{GameName}/{gamename}_text.csv" --out-dir "Projects/{GameName}/TranslateFiles" --no-header

检查 placeholder_report.md。确认机器型标记才进入禁翻表；不要把玩家可见的句型花括号选项误当作变量。详细规则见 placeholder_patterns.md。

### 3. Generate translation guidance

用 ../assets/TranslateDoc_template.md 生成 TranslateDoc.md，并据实际分析结果填写：

- 文本条数、源语言和目标语言；
- 占位符种类、次数与样例；
- ainiee_exclusion_list.json 的导入步骤；
- 必须开启的 AINiee 开关：禁翻表、自动处理文本代码段；
- 项目术语建议和上下文提示；
- 译后校验命令。

AINiee 的界面与设置细节见 ainiee_setup.md。

Every generated translation prompt must include the following block verbatim. Do not
translate it, paraphrase it, omit it, or place it outside the prompt:

```text
###以textarea标签输出译文
<textarea>
1.{target_language}文本
</textarea>
```

### 4. Ask or hand off

在正文翻译前完成固定术语审校。读取 [glossary_review.md](glossary_review.md)，使用共享程序 `Tools/localization/glossary_tool.py` 冻结并检查项目分析表，结合原文审查全表译名、分类和一致性，重点核对错译、人物/物品合并、专名、称谓及可疑条目。生成带依据的人工决定、审校版、修改/隔离清单并校验后，正常退出 AINiee、备份并应用。不要把自动检查通过当成语义审校完成，也不要套用 Inquisitor 的具体译名到其他游戏。

用户要求本流程即包含项目内术语修正与可逆应用，无需重复征询；不扩大为自动启动付费全文翻译。术语尚未提取时明确记录未完成步骤，提取完成后继续；没有术语的项目记录检查为空即可。若原文或术语重新提取，重新审校并记录旧译文需复查。

- 默认：告知用户准备文件已生成，请其按 TranslateDoc.md 在 AINiee 中翻译，并等待译文文件。
- 文本少于 50 条：可以询问是否要 Codex 直接翻译；没有明确同意时，仍只交付准备文件。
- 用户明确授权直接翻译：逐行产出译文，严格保留每个受保护标记、字面 \n、标签和变量；随后执行第 5 步校验。

### 5. Validate a completed translation

运行：

    python "D:\StaticHanHua\.agents\skills\game-translate\scripts\validate_translation.py" "Projects/{GameName}/{gamename}_text.csv" "Projects/{GameName}/{gamename}_text_translated.csv" --fix --fix-newlines --src-col 0 --dst-col 0 --report-dir "Projects/{GameName}/TranslateFiles" --backup-dir "Projects/{GameName}/TranslateFiles/backups"

检查逻辑行和物理行、受保护标记的多重集合、半角字符、变量名，以及字面 \n 是否变成真实换行。退出码 0 才能交接回填。

若生成 need_retranslate.csv：

- 少量问题：仅在用户已授权直接翻译时由 Codex 修复；否则让用户在 AINiee 中重译这些行。
- 大量问题：检查禁翻表与自动处理文本代码段开关后再批量重译。

### 6. Document and commit

更新 README.md 的「翻译」节，记录翻译方式、占位符/禁翻表、术语表与校验结果。

术语审校阶段还要记录原表/审校版数量、人工核对范围、隔离问题与应用验证；提交项目内的原表快照、自动报告、审校决定、审校版、修改/隔离清单、manifest 和相关文档。完整 AINiee 缓存、备份与凭据永不提交。

- 仅准备阶段：提交 README.md、TranslateFiles/TranslateDoc.md、TranslateFiles/placeholder_report.md 和 TranslateFiles/ainiee_exclusion_list.json，提交信息前缀为 文档。
- 翻译完成且校验通过：加入 {gamename}_text_translated.csv，提交信息前缀为 翻译。

绝不提交 Game 目录。

## References

- placeholder_patterns.md：标记识别与保护边界
- ainiee_setup.md：AINiee 设置
- glossary_review.md：固定术语审校流程、共享工具命令与人工决定格式
- ../assets/TranslateDoc_template.md：操作文档模板
- ../scripts/analyze_placeholders.py：占位符分析
- ../scripts/validate_translation.py：译后校验
