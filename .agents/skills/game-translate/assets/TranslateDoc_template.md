# {游戏名} 翻译操作记录

## 路线与输入输出

- 执行路线：{SubAgent 或 API}
- 配置：`workflow=custom`，`backend={codex 或 api}`
- 模型/接口：{实际选择，不含凭据}
- 原文：项目根目录 `{gamename}_text.csv`
- 正式译文：项目根目录 `{gamename}_text_translated.csv`
- 中间产物：`TranslateFiles/`

## 术语与保护规则

- 全文术语提取、真实语义审校和冻结：{版本、数量、审校范围、产物路径及待确认项}
- 占位符分析：`placeholder_report.md`、`exclusion_list.json`
- 已确认保护规则：{配置 exclusions、变量、标签和字面转义说明}
- 文体与背景：{已核实的项目背景和风格要求}

## 执行与验收

两条路线使用 `Tools/localization/translation_workflow.py` 的共用任务、批次和验收规则。SubAgent 由主代理调度；API 由程序请求所选接口并保存真实回复。具体步骤以 `game-translate` 技能及 `references/custom_workflow.md` 为准。

先完成术语冻结和随机样本试译校准，再执行正式翻译与完整独立审校；遗留争议按共用规则处理。所有质量门槛通过后由程序 `export` 导出正式译文，不手工合并 CSV 绕过验收。

- 验证结果：{实际报告路径、结果和未完成项}
- 已知问题：{具体限制或无}
- 回填注意事项：{字体、编码、文本长度等}

完成后同步项目 README 的翻译节与技术栈，提交正式译文和适合入库的配置、审校及交付材料；缓存、备份和凭据不提交。
