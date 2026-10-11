# 固定术语审校步骤

每个游戏在术语提取完成后、正文翻译前执行。共享工具只负责机械检查、保存决定和安全应用，不调用 API，也不决定语义译名。原文或分析表重新生成后再次检查；历史译文不会自动改写。

## 冻结与自动检查

本地 AINiee 的项目表在缓存 `extra.analysis_v1`，包含 characters、terms、non_translate。程序明确支持此格式；其他版本或全局词典先核对并适配，不能猜测字段或替换整个缓存。输入为正式无表头、单列 UTF-8 CSV；行号按 CSV 逻辑记录计数。

在游戏项目目录执行，替换缓存与源文件路径：

```powershell
$glossaryTool = 'D:\StaticHanHua\Tools\localization\glossary_tool.py'
python $glossaryTool audit --cache .\AiNieeCache\AinieeCacheData.json --source .\gamename_text.csv --output .\glossary_review\v1
```

输出 `glossary_original.json`（仅分析表，不含完整缓存）、`glossary_audit.json`、`glossary_audit.md`、`glossary_contexts.json` 与待填写的 `glossary_decisions.json`。检查空译名、同源重复、来源命中、分类、斜杠合成 source 和共用译名。来源匹配为忽略大小写的字面子串，只用于筛查；短名称可能误命中词内，必须读语境。上下文文件每条最多提供两处摘录及原文记录号，必要时查更多原文和资源映射。

既有产物不覆盖，新一轮使用 v2 等新目录。缓存及备份不纳入 Git。

## 人工审校（不能省略）

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

示例不能原样使用，删除不适用的项。edits 更新字段，改 source/译名必须有 evidence_rows；移类或拆分用 quarantine 原条目加 additions，新条目也必须有原文依据。所有索引指向原快照，不随删除改变。gender 支持男性/女性/其他；category_path 支持身份/物品/组织/地名/技能/种族/其他。其他背景分类写入 note，不扩展 AINiee 枚举。

## 构建和应用

```powershell
python $glossaryTool build --snapshot .\glossary_review\v1\glossary_original.json --source .\gamename_text.csv --decisions .\glossary_review\v1\glossary_decisions.json --output .\glossary_review\v1\built
```

生成 `glossary_reviewed.json`、`glossary_review_changes.json`、`glossary_manifest.json`，保留分析表其他元数据，重算统计。未解决的无命中项必须修正、隔离或填写有依据的 allow_no_match。

正常退出 AINiee，让它保存当前项目，然后检查并应用：

```powershell
$applyArgs = @('apply', '--cache', '.\AiNieeCache\AinieeCacheData.json', '--snapshot', '.\glossary_review\v1\glossary_original.json', '--source', '.\gamename_text.csv', '--decisions', '.\glossary_review\v1\glossary_decisions.json', '--reviewed', '.\glossary_review\v1\built\glossary_reviewed.json', '--manifest', '.\glossary_review\v1\built\glossary_manifest.json')
python $glossaryTool @applyArgs
python $glossaryTool @applyArgs --apply
```

默认只检查，`--apply` 才写缓存。核对四份输入 SHA-256，拒绝原表自审校后变化或 AINiee 仍运行的情况；备份完整缓存到旁边 `glossary_review_backups/`，只替换 `extra.analysis_v1`，原子替换后验证整份缓存逐字段符合预期。相同审校版已安装则不重复写。应用进程检查支持 Windows；检查和构建可跨平台。

重新打开 AINiee，确认加载该游戏项目并抽查新译名。README 记录数量、人工核对范围、隔离问题、备份与应用验证；提交小型审校产物和文档，完整缓存/备份不提交。
