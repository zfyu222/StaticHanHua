# PP-OCRv6 medium 与 Qwen3-VL 8B 小样本对照

结论：普通印刷字和规则UI可以优先只用Paddle；这组结果不支持完全取消Qwen。艺术字、手写体和复杂场景中，Qwen能明显补全Paddle的漏字，但极小像素字也会让Qwen产生无关转写，不能把Qwen当成正确答案。

需要区分两种目标：只筛“可能有字”，本样本Paddle原输出覆盖16/16张有字图，现有筛选保留15/16张并排除4/4张无字图，筛选阶段不一定要Qwen；要求原文转写准确，则不能把“检测到了”当作“全文读对了”。

## 范围与方法

- 每类4张，共20张。18张游戏素材/用户截图，2张程序排版的中英文正常文字对照；人工查看图像并在本轮预测前冻结参考原文，标签文件为 `benchmarks/challenge_v1.json`。
- 目的性开发样本，一些难例此前已见过，不能推断全游戏或其他游戏的准确率。没有以这20张调整模型、阈值或提示词。
- 固定 PP-OCRv6 medium 检测+识别、CUDA；Qwen3-VL 8B Instruct Q4_K_M，温度0，无思考模式。两阶段进程串行，两者独立读图，Qwen没有收到Paddle读法或参考答案。主实验20次真实Qwen请求，缓存0，失败0，付费接口请求0。
- 使用现有各自预处理，比较的是可用工作流；Paddle含透明图双背景/放大/大图分块，Qwen是灰底单图/最近邻放大。另补充20张Paddle单图控制以隔离分块合并影响，不改生产扫描器。
- “全对”以归一化字母数字完全一致为准，忽略大小写、标点和空白；不等于逐字标点完全正确。CER为编辑距离/参考字符数，越低越好，缺字和新增字均计错。
- 故障残字0130.png的完整读法依赖项目历史档案，不能独立从当前帧读全，只作诊断，不计主CER/全对率。其余15张含字图共484个参考字符。Qwen的uncertain算待审，不能算成功转写；无字图片单列误报，不能使用CER。
- 手写便条裁掉无法确认的签名，报纸贴图裁掉装饰性小字，对两模型使用相同裁图。完整2560×1600截图保持整图输入，仅对其中可读报纸区域评分；其他区域的输出保留在原始记录，不自动认定为幻觉。

## 结果

| 类别 | Paddle 原工作流 | Qwen 8B |
|---|---|---|
| 正常字：4张 | 4/4全对，CER 0% | 4/4全对，CER 0% |
| 艺术字：4张 | 1/4全对，CER 8.30% | 3/4全对，CER 0.35% |
| 像素难字：3张可评分 | 0/3全对，CER 50% | 1/3全对，CER 60% |
| 复杂场景：4张 | 1/4全对，CER 31.82% | 4/4全对，CER 0% |
| 无字：4张 | 原始识别4张误报；已有0.6/英文筛选后0张 | 0张误报 |

主CER合计：Paddle原工作流62/484=12.81%，Qwen13/484=2.69%。这是这批参考字符的错误比例，不是总体准确率；长便条在字符加权指标中占比较高。

Paddle的场景数字受到程序合并误差影响：完整截图分块产生 `INFANT NT`、`CONTINUE! NUE!` 等重复。只用原始整图的一次检测/识别，场景CER降至6.82%，全部样本主CER降至10.74%，但小像素图和透明标题又有退化。单图控制不是整体更优的配置；也不能把原流程的全部重复归因于OCR模型能力不足。本次未修改合并逻辑，保留原流程及单图结果供复核。

## 值得看的实例

- 装饰海报：参考 `TREASURE OF THE VAMPIRE REDNECKS`；Paddle只读到 `TREASURE OFTHE`，Qwen完整读出。只看已有行的高分，发现不了未检测到的大标题。
- 哥特体报头：Paddle为 `The Lakebre b post`，Qwen为 `The Lakeview Post`；下方规则大字Paddle也基本正确。
- 窄标题：Paddle完整读对 `LAKEVIEW CABIN III`；Qwen多出一个Y，成为 `LAKEYVIEW CABIN III`。
- 70×7像素 `GAME OVER`：Paddle为 `GAMEOYER`，Qwen却写成 `STANDARD`。这是明确的无关转写，Qwen不能替代人工真值。
- 34×7像素 `DRAW`：Paddle为 `BH`，Qwen为uncertain。两者都没读对，Qwen这次保留了不确定性。
- 不规则 `COME PLAY`：Paddle只读出 `SOME`，Qwen读对。
- 现有英文筛选会删去重复字母 `III`，导致Paddle原来正确的窄标题在筛选输出中缺字。这是规则损失，应与模型读错分开看。

## 耗时与建议

RTX 5080 16GB，实测无缓存。Paddle模型初始化9.75秒，20图处理5.37秒，总计15.12秒；Qwen本地调用链48.27秒（包括模型加载，不包括服务启动），服务端累计47.46秒，其中加载5.34秒。接口、预处理和统计边界不同，不将二者简单相除作为硬件吞吐倍率。

建议保留Paddle作主力，将Qwen改为可选复核层：普通字体/规则UI优先Paddle，艺术字、手写体、复杂背景、读法冲突及极小文字进入复核；无字筛选先用现有廉价规则。极小字的Qwen输出也必须检查。高识别分数不保证没有漏字；这一小样本也不能证明所有普通文字都能安全跳过复核。

这次只完成对照实验，没有改动主工作流的默认路由、正式原文/译文或游戏安装文件。

## 重现与证据

```powershell
Tools/image_text/.venv-gpu/Scripts/python.exe Tools/image_text/compare_models.py all --work D:/ImageTextComparisonNew
```

输入来源及截图路径见标签文件。原始素材不入库；首次准备后，实验输入快照保存在输出目录，后续读图不依赖临时截图路径。需要重新测量时使用新的输出目录。复跑已有目录可能读取Qwen缓存，其耗时不能作为新一次推理耗时。

- 图像＋逐例对照：`verification/comparison_v1/comparison.html`
- 完整评分：`verification/comparison_v1/comparison.json`
- 原始OCR：`verification/comparison_v1/run/scan.json`
- Qwen原始HTTP回复、推理用量与建议：`verification/comparison_v1/run/local_vlm/`、`vision_findings.json`
- 单图控制：`verification/comparison_v1/paddle_single.json`
- 可入库精简评分：`reports/comparison_v1.json`
