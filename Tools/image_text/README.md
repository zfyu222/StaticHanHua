# 游戏图片文字扫描工具 v0.1

用于静态汉化的图片文字发现、OCR提取和人工确认。输入已导出的图片目录，输出来源清单、原图文字框、OCR候选、疑难/漏检抽查队列、本地审核页，以及确认后的单列CSV与回填映射。独立于游戏引擎，不启动游戏、不操作桌面、不改输入图片、不自动翻译或部署。

## 安装与运行

Windows / Python 3.12，推荐独立环境。工作区根目录执行：

```powershell
Tools/image_text/setup.ps1
Tools/image_text/run.ps1 inventory --input D:/ExportedImages --out D:/ImageTextWork
Tools/image_text/run.ps1 scan --out D:/ImageTextWork
Tools/image_text/run.ps1 review --out D:/ImageTextWork
Tools/image_text/run.ps1 queue --out D:/ImageTextWork --max-jobs 100 --negative-samples 20
```

输出目录必须位于输入目录之外，避免扫描自己的输出。安装使用requirements-lock.txt固定本次验证的依赖版本；requirements.txt列出直接依赖。库和模型安装只发生在工具环境中；默认模型随RapidOCR包提供，其他语言可能需要首次下载模型。默认纯CPU，不需要显卡，不读取API密钥。`run.ps1`只调用隔离环境；也可直接运行 `.venv/Scripts/python.exe cli.py ...`。

自行打开 `D:/ImageTextWork/review.html`。支持筛选、资源名/文字搜索、放大、深浅底色、文字框定位、修订文字、确认/忽略、鼠标框选漏检文字。页面无在线依赖；下载审核JSON保存工作，之后可重新载入。浏览器本地暂存只作便利功能，不能替代下载备份。

```powershell
Tools/image_text/run.ps1 export --out D:/ImageTextWork --review D:/Downloads/review-decisions.json --csv D:/Project/game_images_text.csv
```

只导出明确确认的非空文字，同文去重；每个显示位置及全部重复图片来源保留在 `game_images_text.mapping.json`。CSV为UTF-8 BOM、单列、无表头，多行文本使用标准CSV引号。导出拒绝覆盖已有文件。遗漏和待审核内容不会自动视为通过；导出的只是已确认子集，不是全图审核通过证明。不要直接覆盖已有游戏正式原文或翻译进度。

## 扫描策略与状态

检测与识别分开调用；识别为空、低分或失败时，检测框仍保留。透明图片铺深/浅底；小图最近邻放大；大于960像素的大图补768像素重叠分块，原图坐标保留。完全相同的可见RGBA像素去重，透明像素背后的不可见RGB统一；不以近似图去重，避免丢失细微文字差异。

| profile | 策略 |
|---|---|
| `fast` | 深浅底、最近邻放大、大图分块 |
| `balanced`（默认） | fast；未检出的小图再用平滑放大补扫 |
| `careful` | 小图同时使用最近邻和平滑放大，保留不同读法 |

`--sprite-crops`可额外扫描来源元数据里的精灵矩形，适合小字图集，额外消耗CPU。不默认扫描每个角色部件。`--language`选择RapidOCR语言，默认随包提供的中英可识别模型；并未验收其他语言模型，选项须与后端实际支持匹配。检测阈值默认0.35，优先保留疑似文字；`--threads`默认4。

| 状态 | 含义 |
|---|---|
| `text_candidate` | 至少一个非空、高识别分数且各次读法无冲突的候选；仍需确认 |
| `needs_review` | 只有空结果、低分或冲突候选，可能只是纹理 |
| `not_detected` | 未检出，不等于没有文字 |
| `scan_failed` | 至少一个处理步骤出错，不能判为无字 |
| `not_scanned` | 未扫描（部分执行时在审核页显示） |

识别分数不是校准后的正确率。高分也可能读错。像素字体、徽标、碎纸谜题、手写字、旋转/弯曲文字和极小字仍需复核；本版没有自动旋转搜索、第二个独立检测模型或语义版面合并。字母/数字/签名是否翻译由审核决定。

## 缓存、恢复与队列

逐图落盘，可重新运行同一命令接续。缓存签名包含图片像素、模型文件SHA-256、库版本、预处理版本、profile、阈值及精灵裁剪；更换输入/设置不会悄悄混用旧扫描。不同设置请使用独立工作目录。扫描失败会保存诊断但不缓存为成功；`--refresh`显式重算所选图片。

可用 `--ids hashes.json` 指定图片哈希列表，或用 `--limit N`进行小样本试跑；未处理图保持未扫描。图片快照哈希会在读取缓存前核验。审核文件绑定完整扫描哈希；扫描继续或修改后旧审核文件拒绝直接导入，不静默迁移确认结论。

`queue`准备离线复核包，不发送多模态请求。`--max-jobs`限制任务数；优先预留按尺寸分层抽取的未检出样本，然后错误、读法冲突、普通低分、大量小精灵疑似纹理。记录完整原图、带边距裁剪、原始坐标、来源和短编号；超出预算的项目明确计为deferred。抽查数量不足时报告实际数量。

本版尚未实现付费多模态适配器、回复自动导入和价格计费。候选可在审核页确认；不能把离线队列已生成说成多模态已审查。以后接接口时沿用用户明确选择，增加模型/提示词缓存、预算及真实小样本验证，再接入相同人工确认/导出门槛。

## 引擎来源适配

输入普通图片文件夹即可。可通过 `inventory --provenance metadata.json` 加入以相对文件名为键的来源映射：

```json
{
  "atlas.png": {
    "asset": "sharedassets3.assets",
    "path_id": 123,
    "texture_name": "Signs",
    "sprites": [{"name": "Signs_0", "box": [10, 20, 90, 45]}]
  }
}
```

坐标统一为**原图左上角原点、像素单位、left/top/right/bottom**。Unity的底部原点矩形须在适配时转换。与引擎相关的信息透传，不被OCR丢弃。当前不直接解析Unity包、加密资源或游戏存档；复用既有提取工具后输入图片。`prepare_lakeview.py`演示来源转换及已知样本准备，不改项目文件。

## 接入汉化工作流

1. 解包阶段导出图片与资源定位信息，调用本工具扫描。
2. 复核候选、抽查未检出，记录保留的签名/谜题等；只把确认文字导出为独立图片源文CSV。
3. 交给现有 `game-translate` 共用流程，仍须术语整理、试译、正式翻译和审校，不把OCR候选直接当正式原文。
4. 回填阶段通过mapping把译文定位到图片区域，选择原图替换或独立覆盖层；本工具不代替排版与实机验收。

## 验证

```powershell
Tools/image_text/.venv/Scripts/python.exe -m unittest discover -s Tools/image_text -p test_*.py -v
Tools/image_text/.venv/Scripts/python.exe Tools/image_text/prepare_lakeview.py --project Projects/lakeview-cabin-collection --out Tools/image_text/verification/lakeview-sample --include-negatives
```

准备75张已知含字图片及12张人工看过的无字小精灵。按上述命令inventory/scan后，可运行 `benchmark --out <扫描目录> --truth <准备目录>/truth.json`。

benchmark分别报告有候选的正例图片数、较稳定候选数、已知宽区域命中数、无字样本误报数。宽区域命中不是逐字准确率；这是开发样本，不是独立测试集，更不能据此宣称全游戏召回率。首次fast测试73/75有候选；默认补扫后75/75有候选，但部分字仍读错，118个已知区域仍有未命中。当前没有完整文字转写准确率基准。

UI无窗口测试：`node Tools/image_text/test_browser.cjs <扫描目录>`。测试依赖Playwright及Edge，可通过 `PLAYWRIGHT_MODULE`、`EDGE_PATH`指定本地路径。测试使用独立浏览器上下文，产生带“Manual fixture”的验证审核文件，仅用于导出链路测试，不是正式游戏原文或人工验收结果。

实际测量见 `reports/validation.json`；大体积图片、模型、缓存和临时运行文件在 `verification/` 或 `.venv/` 中，不提交。上游项目：[RapidOCR](https://github.com/RapidAI/RapidOCR)、[PaddleOCR检测文档](https://www.paddleocr.ai/main/en/version3.x/module_usage/text_detection.html)。分发工具及模型时应保留各自许可与来源，本仓库不重新打包模型。

### 本轮实际结果（2026-10-23）

- 全部1666张去重图片逐图本地处理，3796个候选文字区，处理错误0；首轮扫描455.875秒，缓存接续总耗时13.494秒（其中模型初始化1.532秒），1666/1666命中缓存，扫描与审核身份保持一致。
- 图片分类：235张较稳定候选、1155张仅疑似文字、276张未检出。当前策略重视减少漏检，疑似纹理较多，不能将这些计数当作真实含字图片数量。
- 开发样本：75/75已知有字图片存在候选，其中68张有较稳定候选；118个已知宽区域命中101个。12张无字小精灵中11张出现疑似框，0张达到较稳定候选层。样本较窄、参与过调参，不能据此宣称泛化准确率。
- 已生成100项离线复核任务，包含20张未检出抽查；剩余3215项候选延后，不意味着通过。队列按区域计数，同一原图可能有多个任务。
- 13项行为回归及整套1666张数据的无窗口UI测试通过。模型API调用0；没有改变游戏文件或翻译项目状态。
- 完整审核页位于本机 `Tools/image_text/verification/full_lakeview/review.html`；该页面为本轮本地生成产物，不纳入版本库。正式使用请下载自己的审核文件，勿载入 `browser-test-review.json`（其中有用于测试的“Manual fixture”）。
