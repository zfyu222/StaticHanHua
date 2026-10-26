# 禁翻作用域改进：本地验证

日期：2026-10-26。未调用真实或模拟 API 客户端，付费请求 0；测试不使用生产数据库，不启动游戏。

实际运行并通过：

```powershell
# 工作目录 Tools/localization
python -m unittest test_nontranslate_policy test_glossary_corpus_evidence test_glossary_tool
# 当时的新测试10项；合计30项，通过。

python -m unittest test_nontranslate_policy test_translation_workflow.WorkflowTests.test_glossary_revision_only_invalidates_affected test_translation_workflow.WorkflowTests.test_minor_prompt_change_preserves_translation_and_acceptance test_workflow_drafts
# 新测试增至12项；合计33项，通过。
```

两次共覆盖 53 个不同测试，包含完整冻结拦截、全局保护双语境引用、旧表兼容、缓存例外清理与原日志不变、后续无关修订的证明延续、候选报告不修改译文、局部审校范围保留和原有草稿检查。

随后扩展两字母残留（如 go）的检测并复查报告术语哈希绑定，相关单项回归通过；没有再次扩大模型执行范围。

`offline_replay.py` 只读 Avadon 的真实原文和旧冻结表，证实新规则会拦截无作用域的 retrain，并完整提供第7192条普通句和第15333条独立字串；显式 whole_record 可保留独立字串。当前65条混合候选只是候选，不是65处错误。源文/译文/旧冻结表和22个已安装载荷哈希保持不变，结果在 report.json。

限制：验证的是本地确定性检查、任务输入构造和状态处理，没有测试新提示下模型的实时回答效果，也不保证语义审校零漏判。没有使用模拟 API 通过结果冒充真实接口验证。
