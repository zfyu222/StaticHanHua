# {游戏名} AINiee 翻译操作指南

> 本文档由 game-translate Skill 生成，指导使用 AINiee 完成本游戏的文本翻译，
> 核心是**防止 AI 破坏占位符与格式化字符串**。按顺序执行即可。

## 一、翻译前必做：占位符防护

本游戏原文共 {N} 行，检测到以下占位符类型（详见 `placeholder_report.md`）：

{占位符类型列表，示例：}
| 类型 | 出现次数 | 示例 |
|------|----------|------|
| 方括号变量/标签 | {n1} | `[Player:Name]` `[i]` |
| printf 格式符 | {n2} | `%s` `%d` |

### 第 1 步：导入禁翻表

1. 启动 AINiee：`cd Tools\AINiee && .\venv\Scripts\python.exe AiNiee.py`
2. 打开左侧导航「**禁翻表**」，打开顶部开关
3. 点击工具栏「**导入**」，选择本目录下的 `ainiee_exclusion_list.json`
4. 点击「**保存**」

禁翻表内容（已在 JSON 中，无需手填）：
{禁翻表条目列表，示例：}
| 备注 | 正则 |
|------|------|
| 方括号标记 | `\[/?[A-Za-z][A-Za-z0-9]*(:[A-Za-z0-9/]+)?(="[^"]*)?\]` |

### 第 2 步：开启自动处理文本代码段

进入「**翻译设置**」，打开「**自动处理文本代码段**」开关。
该功能译前把禁翻表命中的内容替换为 `[P0]` 占位符、译后还原，是防破坏的硬保障。

### 第 3 步：提取并审校术语表（必须）

先使用 AINiee 全文分析提取项目术语，然后冻结原表并运行共享工具 `Tools/localization/glossary_tool.py audit`。结合原文审查来源、译名、分类和一致性，保存审校决定、修改/隔离依据，再用 `build` 生成审校版。正常退出 AINiee 后，先以 `apply` 检查，再加 `--apply` 备份并安装；验证翻译结果与进度保留。项目分析 JSON 不能导入全局词典。详细命令与格式见 `.agents/skills/game-translate/references/glossary_review.md`。

本项目审校记录：{审校目录、原表/审校数量、重点语境核对范围、待确认项、应用及备份验证结果}。未提取术语时本步骤保持待办，不能直接标为完成；无术语时记录检查结果。

本项目固定译名示例（须经审校后使用）：
{术语表示例，示例：}
| 原文 | 译文 | 备注 |
|------|------|------|
| thegn | 领主 | 称谓 |
| Skjern | 斯基恩 | 地名 |

### 第 4 步：附加提示词（推荐）

在提示词设置中追加：
```
这是游戏文本翻译。所有方括号标记（如 [Player:Name]、[i]）、花括号变量、百分号格式符必须原样保留，不得翻译、不得增删、不得转换全半角。

###以textarea标签输出译文
<textarea>
1.{target_language}文本
</textarea>
```

## 二、翻译

1. 「接口管理」添加并激活 API（推荐 DeepSeek）
2. 将 `{gamename}_text.csv` 拖入输入框
3. 源语言 {源语言} → 目标语言 简体中文
4. 点击「开始翻译」，等待完成
5. 输出保存为 `{gamename}_text_translated.csv`

## 三、翻译后必做：完整性校验

```powershell
python ".codebuddy/skills/game-translate/scripts/validate_translation.py" "{gamename}_text.csv" "{gamename}_text_translated.csv" --fix --fix-newlines
```

校验项：行数对齐 / 标记多重集合一致 / 标记未被中文化 / 无全角转换 / 字面 `\n` 未变真实换行。

- 输出 `need_retranslate.csv` → 其中行需重翻（少量，可手工译或 AINiee 重翻后合并）
- 全部通过 → 进入第 3 步打包回填（game-repack Skill）

## 四、常见问题

| 症状 | 原因 | 处理 |
|------|------|------|
| `[名字]` 被译成中文 | 禁翻表未开启/未保存 | 重查第一节步骤 |
| `【...】` 全角括号 | AI 全角化 | validate --fix 自动还原 |
| 物理行数变多 | 字面 `\n` 变真实换行 | validate --fix-newlines |
| `{选项文本}` 没翻译 | 误把通用 `{}` 加进禁翻表 | 从禁翻表删除该条，重翻相关行 |
