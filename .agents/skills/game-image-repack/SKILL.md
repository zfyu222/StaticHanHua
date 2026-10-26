---
name: game-image-repack
description: Merge formally translated image text into game image patch resources or runtime-overlay manifests after translation. Use for 图片文字合入、中文贴图、图片汉化排版、文字蒙版覆盖; includes preview and engine handoff, not OCR or ordinary text translation.
---

# 游戏图片文字合入

读取项目README、`localization_project.json`、已确认图片原文/mapping、正式图片译文和对应 `final_validation.json`。用户选择启用才加入此步骤；已授权不重复询问。保持常规文本译文、图片文字和已有补丁状态独立。

本技能位于 `game-translate` 之后、引擎最终回填之前。图片翻译必须完成共同流程的语义验收，不能将模型即时输出直接画回游戏。原文/译文的单列CSV逻辑记录数必须对应，报告哈希必须匹配。

## 选择载荷方式

- **texture**：给可安全替换的贴图输出同尺寸RGBA PNG；先按原图风格处理旧文字，再美观地绘制中文，保留底图。不能以不透明/近不透明的统一色块遮住原图来掩盖原字。原图保留在工作快照，不覆盖原始素材。
- **overlay**：输出中文、原图坐标、来源和蒙版参数，交运行时覆盖层适配器。适合原字区过小、精灵图集共享、动态显示或需要切换原文的情况。

程序排版时检查字体实际字形覆盖和文本是否放得下；不能把缺字方框、裁切、过小字号当成功。保持UV、pivot、精灵切片和透明边缘。不同图片/区域可分批使用不同方式，最终由引擎适配器整合。

## 视觉处理原则（所有游戏适用）

优先沿用原图的字形风格、配色、位置和层级：透明标题/按钮保留透明底；纯色面板按原色处理；地图、纸张和场景贴图只在已确认文字笔画附近清字、局部修补，再绘制中文。需要提高可读性时优先细描边/阴影；确需底色时使用与原画协调的小范围低透明度底色，不铺大灰块。默认蒙版alpha=64/255（约25%不透明度）并加1像素描边，仅是候选起点，不是所有素材的最终样式。允许逐图配置颜色、透明度、描边及清字方式；不得因为原字重影而简单加深色块。

`--texture-styles <JSON>`提供按image_id绑定的局部样式。`erase_text_ink`是为地图等特定配色准备的局部颜色检测/inpaint，不是通用清字器；不能直接套到人像、复杂插画、图标/共享图集。原字无法干净清除或隐藏细节无法可靠修补时，换用适合素材的清字/重绘或overlay方案。不要宣称降低alpha就完成美化，也不要自动覆盖已完成项目的载荷。

必须查看原图与中文预览，检查原图关键细节是否被遮挡、旧英文重影、修补痕迹、颜色/字体协调、字号和裁切。地图/插画/透明按钮等不同类别各抽取代表性全图，并回看所有异常区域。构建清单的`visual_review`默认pending；只有实际看过预览并记录结果才算完成视觉检查，程序通过和双模型一致都不能替代。引擎导入方式仍按游戏单独适配，不能承诺任意游戏自动得到相同效果。

## 构建与静态校验

`$py`为 `Tools/image_text/.venv-gpu/Scripts/python.exe`，合入本身用CPU，不需要加载GPU模型。

```powershell
& $py Tools/image_text/image_workflow.py build --out <图片工作目录> --source <项目/{game}_images_text.csv> --translated <项目/{game}_images_text_translated.csv> --validation <图片子工程/TranslateFiles/reports/final_validation.json> --font <中文字体文件> --destination <项目/ImagePatch/build-v1> --mode texture
```

使用overlay时将mode改为overlay。输出目录须全新，不覆盖旧载荷；若文字无法放进原矩形，检查 `BUILD_FAILED.json`，增大经过确认的覆盖区域或改用运行时overlay，不擅自改贴图尺寸。修改原文位置需保留审阅证据，不能篡改旧确认映射来掩盖问题。

检查 `IMAGE_PATCH_MANIFEST.json`、`preview.html`、实际输出PNG/overlay条目：中文内容、字号、换行、边界、背景透明度和来源映射。程序构建通过不等于实际视觉排版通过，Agent需查看预览；大批量可先联系表，再回看疑难原图。

## 引擎合入与用户测试

构建输出明确标为 `ready_for_engine_adapter`、installed=false。PNG/JSON不是可直接安装的通用补丁。交给 `game-repack`：

- Unity恢复Texture2D/Sprite及bundle来源；处理图集切片、压缩格式、mipmap、pivot和紧密网格。overlay模式须有与当前游戏匹配的运行时适配器。
- RPG Maker按真实图片相对路径生成替换载荷；核对图片尺寸/引用方式。
- 其他引擎先定位载入、打包和绘制方式；原生逆向沿用REA+Ghidra。没有适配器时报告资源已生成、引擎导入待完成，不宣称已安装。

沿用项目已有的备份/载荷哈希验证和部署授权。游戏正在运行时由用户自行退出，不能控制其桌面。用户明确偏好自行测试时提供场景、原文开关、分辨率和预期显示检查点；没有实际测试记录就保持待测。不得自动打包发布或把游戏原始资源纳入补丁ZIP。

完成后更新README「图片文字合入/回填/已知问题」，记录方式、字体哈希、支持版本、载荷、静态/用户实测边界；在项目独立仓库提交文档与小型映射/清单，大图和二进制按体积排除。
