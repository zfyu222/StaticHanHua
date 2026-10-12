# 翻译流程工具

自定义工作流的程序入口是 `translation_workflow.py`。原 AINiee GUI 工作流保留；入口选择及子代理派发由 `game-translate` 技能管理。所有翻译中间产物放在游戏汉化项目 `TranslateFiles/`，正式原文/译文保持项目根目录的单列 CSV 格式。

## 运行

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
  "batch_records": 60,
  "batch_chars": 12000,
  "context_records": 2,
  "polish": false
}
```

已有配置恢复时不重新 init。`--context` 可读取提取时的上下文映射：JSON 对象按源文逻辑记录号索引，值可包含 group、speaker、resource、scene、text_type、note；各字段为字符串。group 用于避免跨场景合批，speaker 可让程序补充相关人物术语。

init 默认从项目 README 的游戏信息、简介、背景设定、世界观和文体等章节自动读取游戏背景，保存到 TranslateFiles/config/game_background.json。Luna 与 API 的每阶段任务都会携带这份快照、文体及相关上下文。可用 game_background 字符串替换为已核实的背景摘要；设为 null 则自动读取，设为空字符串则不载入背景。运行中更新 README 后需暂停并 configure 重新读取，不会自动改变在途任务。背景缺失时不推测剧情。

AINiee 选择为 `workflow=ainiee`；init 只生成准备文件，不调用翻译。新 GUI 项目导入 TranslateFiles/source 下的源文快照，保持“项目缓存随输入目录”开启，缓存即可留在 TranslateFiles 内。旧项目缓存不自动移动或覆盖，原始 GUI 入口及使用方式继续可用。

## 程序接口

| 命令 | 用途 |
|---|---|
| init / status | 保存选择及快照、占位符分析；查看进度与异常任务 |
| prepare --stage ... | 按阶段生成任务，重复调用不重复创建已覆盖任务 |
| claim --stage ... --worker ... | 事务领取任务、生成独立任务文件与租约 |
| prompt --task-file ... | 为已领取任务生成协议提示词文件 |
| submit --task ... --token ... --result ... | 对 ID、版本、标记、术语及语义审校状态验收并提交 |
| freeze-glossary | 合并所有审校决定，复用 glossary_tool 构建后冻结 |
| reopen-review --task ... --reason ... | 冻结前修正已提交术语审校；保留轮次限制与历史 |
| fail / recover / heartbeat | 失败记账、核查旧 worker 后恢复、延长租约 |
| pause / resume / configure | 停止新任务、恢复、通过配置补丁创建新修订 |
| revise-glossary | 带来源哈希的术语语义审校完成后冻结修订，仅重置受影响记录 |
| lock / unlock | 有理由地保护或解锁确认译文 |
| run-api --stage ... | 程序执行 API 任务；并发、限流、延迟重试及请求预算 |
| export | 必需阶段和最终检查通过后原子导出，并备份已有正式译文 |

阶段依次为 extract → glossary_review → freeze-glossary → translate → review →（可选 polish → final_review）→ export。每个阶段内部任务可并行，由 AI 读取相关任务、术语和上下文。正文子代理不负责新增术语。

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

## 复用与验证

AINiee 基线、模块 hash 和来源记录在各运行的 source/manifest.json；本地术语匹配修复直接复用，保护规则复用原占位符分析器，标记还原复用 TextProcessor。新增不碰撞的保护编号和不限 50 个的标记处理，避免旧处理器的批次数量限制。术语审校复用 glossary_tool.audit/build。

本地 AINiee 为 AGPL-3.0，保留其 `Tools/AINiee/LICENSE` 和来源说明。适配器无全局配置读写，未改动 AINiee 源码。

```powershell
& $python -m unittest discover -s Tools/localization -p 'test_*.py' -v
```

测试覆盖并发领取、来源与版本变化、旧结果拒绝、完整术语前置门槛、审校门槛、单列 CSV 对齐、锁定译文、增量复查、保护标记及模拟 API 预算。真实 Luna 并行验证报告见 verification/luna_smoke_report.json；样本为合成文本，不代表已验证所有游戏语境。真实 API 请求尚未测试。

运行队列和大缓存默认忽略；项目配置、术语审校材料、必要变更记录和交付报告可以入库。技能参考 `references/custom_workflow.md` 承载具体 AI 调度规则，基础文档无需列出内部子阶段。
