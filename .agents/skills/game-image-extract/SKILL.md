---
name: game-image-extract
description: Extract and visually confirm text from exported game images before translation, using GPU Paddle OCR, selective local Qwen review and the current multimodal conversation agent for disputes. Use for 图片文字抽取、贴图OCR、图片漏字复核; not for translating or installing image patches.
---

# 游戏图片文字抽取

在 StaticHanHua 根目录工作。先读项目README、`localization_project.json` 和 `Tools/image_text/README.md`。用户已选启用时沿用；disabled则跳过，undecided才问是否加入图片文字翻译。当前明确授权优先于旧配置，更新记录并保留旧产物。

本技能位于常规解包之后、正式翻译之前。需要先从引擎导出图片时由 `game-unpack` 处理；输入是已导出的图片目录及可选引擎来源JSON，不直接遍历游戏二进制猜原文。三个模型职责、判断边界见 `Tools/image_text/WORKFLOW.md`。

## 抽取与复核

以下命令在工作区根执行；`$py`为 `Tools/image_text/.venv-gpu/Scripts/python.exe`，`$flow`为 `Tools/image_text/image_workflow.py`。

1. 用现有 `cli.py inventory --input <导出图片> --out <项目/ImageText/work>` 创建去重快照；有来源JSON时加 `--provenance`。不要覆盖已绑定旧队列/审阅结果的目录。
2. 新游戏先看小样本校准资源族，将已验证普通字体、字高范围、视觉校准依据和高风险资源写入策略JSON。参考 `references/policy.md`；缺少校准时保持unknown，不因高分自动声明普通字体。
3. 执行三级管线：

   ```powershell
   & $py $flow pipeline --project <项目> --out <项目/ImageText/work> --policy <策略JSON>
   ```

   全量GPU medium OCR在子进程完成并退出，然后Qwen只处理路由指定难例和抽查。Qwen模型缺失时按工具README下载固定本地模型；不擅自换云接口。已有完整扫描可用 `plan`、`qwen` 分别执行，不能把旧缓存强行改成新模型结果。
4. 管线产生Agent包。继续执行 `agent-pack --out <work> --limit 20` 取得待审短编号和包路径；任务在 `tiered/agent/<包ID>/input.json`，绑定信息在 `package.json`。当前对话Agent直接查看真实原图与清晰证据图；普通候选可批量看清晰裁图，争议逐图放大。候选仅作假设，不按模型投票，也不根据剧情补字。
5. Agent写回复JSON：

   ```json
   {"records":[{"id":"1","status":"text","reason":"图中可见…","unreadable":false,"regions":[{"text":"GAME OVER","box":[0,0,1000,1000]}]}]}
   ```

   `status`为text/no_text/uncertain；后两者regions为空。部分可读时保留可读区域并设unreadable=true。不能看清时保留未决，需要实机场景则给用户步骤，由用户自行测试，不控制电脑。不要把此示例伪装成实际结果。
6. 导入真实裁决：

   ```powershell
   & $py $flow agent-import --out <work> --package <package.json> --reply <实际回复JSON> --reviewer conversation_agent --model-label <用户当前选择的模型标签>
   ```

   用户实际确认才使用 `--reviewer human`。程序绑定短编号、图片和坐标；Agent不用抄哈希。缓存成功项不重复派发；继续取包直到需处理范围结束。无法辨认的项不无限重试。
7. 导出明确确认的单列原文和独立mapping：

   ```powershell
   & $py $flow export --out <work> --csv <项目/{game}_images_text.csv>
   ```

   默认拒绝未审/未决范围；仅交付已确认子集时可显式 `--allow-partial`，并报告未决数量。预测排除和双模型一致不能充当视觉确认。既有CSV拒绝覆盖。

## 交接翻译

运行 `prepare-translation --project <项目> --source <项目/{game}_images_text.csv>`，在 `TranslateFiles/image-text/` 建立隔离的翻译子工程、源文快照和逐条图片上下文，不动常规文本翻译状态。

使用 `game-translate`，将该子工程作为 `--project`，使用其根目录的 `*_images_text.csv` 和 `image_context.json`；沿用用户已选SubAgent/API路线、术语审校和完整语义验收。不要因为准备数据集就启动未授权的正文翻译。完成后 `collect-translation --project <原项目>` 校验正式导出哈希，并把 `{game}_images_text_translated.csv` 收回项目根目录。

本技能只抽取，不翻译和安装。完成后更新README「图片文字提取/已知问题」，记录校准、模型/工具版本、分流/复核/未决统计，提交原文、mapping和适合入库的策略/报告；图片快照、模型、缓存和日志不入库。
