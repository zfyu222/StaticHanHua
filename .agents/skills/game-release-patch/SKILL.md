---
name: game-release-patch
description: Generate an external-release game localization patch ZIP under Projects/{GameName}/Dispatch, including translated files, a Chinese installation guide, manifest, and checksums. Use when the user asks to publish, release, distribute, package for other players, create a patch ZIP, or create/update 补丁说明 for a completed game translation.
---

# 发布汉化补丁

将已实测的汉化制作成玩家可直接使用的ZIP，并发布到游戏独立公开GitHub仓库。应用本技能时同时维护工程README，但不要把工程证据复制进玩家说明。

## 玩家说明规则

标题之后第一项必须是安装说明，只写一句，例如“解压到 Avadon.exe 所在文件夹，双击‘安装汉化.cmd’。”或已验证可用的“解压到游戏根目录即可”。随后依次写简短的补丁升级、卸载说明。优先提供安装/卸载脚本；没有可用脚本时先实现并验证，不能把手工逐文件处理推给玩家，也不能声称存在未提供的脚本。

禁止教玩家备份、逐个复制文件、手动删除文件；不加入MOD冲突等防御性描述。自动备份、版本核验和失败处理由安装程序负责。用户只需解压、双击。升级说明只写覆盖解压后运行安装脚本。卸载只写运行卸载脚本；旧包缺少原件时可由脚本自动调用Steam恢复。

接下来可简写介绍、适用版本、覆盖内容、实际正常的功能及AI汉化提示。“运行情况”只写如“开门提示、消息栏、快捷键和地图显示正常”。不写用户答复、代理操作、验收经过、哈希绑定、未测项目、提取子集、未决图片数、底纹算法、显卡/MOD等技术边界；这些只保留在工程README与验证报告。不能夸大已正常的范围。

下载区必须按以下顺序连续写，订阅提醒紧接下载地址及可用网盘地址，之后紧接反馈：

```
下载地址:https://github.com/<owner>/<game-repo>/releases/
或者网盘通过网盘分享的知识：<分享名称>
链接: <网盘链接>
请订阅github地址或者网盘地址，以便在补丁更新时获得通知
有问题请反馈至【腾讯文档】<游戏问题反馈>
<腾讯文档链接>
```

网盘/腾讯文档链接是每个游戏独立配置，不得套用其他项目或编造。先查已有配置，缺少时向于鸿鹄询问；若当前没有已确认的联系方式，在当前对话请用户提供或转达，不自行猜测身份或地址。本游戏的固定地址放在本游戏Release配置。TXT和GitHub Release正文使用同一份简明说明。更新日志必须在文末保留历次内容。

## 执行与验证

1. 读取README和回填报告。存在自定义翻译数据库时，执行共用流程的release-check，正式译文、实测载荷及证据必须匹配。只增加安装包装时保留原翻译实测记录，对脚本另做实际安装/升级/卸载回放，不伪造新的游戏实测。
2. 版本从Dispatch现有最高版本加0.1，首次1.0。固定名`{游戏名}_子非鱼汉化{版本号}.zip`，标题`《{游戏名}》子非鱼AI汉化补丁 v{版本号}`。保留旧版，不覆盖已发布ZIP。
3. 用scripts/create_release_patch.py先dry-run，准备Dispatch/Files并核对，ZIP仅从Files的已选文件生成。直接解压模式保持游戏相对路径；`--installer-config`脚本模式把载荷放`汉化文件/`，根目录提供`安装汉化.cmd`与`卸载汉化.cmd`，调用包内PowerShell，无需Python。清单同时记录实际目标与archive_path，脚本辅助文件单列，不绕过实测载荷门槛。
4. 用`--install`、`--upgrade`、`--uninstall`传入各一句话；`--verification`只传正常功能摘要。提供`--download-url`，以及已确认的`--mirror-url --share-name --feedback-url --feedback-name`。升级须`--changes`，累计更新日志自动从旧ZIP读取。TXT用UTF-8 BOM。
5. 不打包Game联接、游戏原EXE、原始游戏资源、凭据或开发日志。验证ZIP CRC、完整目录、所有清单哈希、脚本正常安装/重复安装/升级/卸载、旧版升级及异常时保留原文件。版本核验与路径约束放程序内部，不写成玩家检查表。
6. 在游戏独立Git仓库提交小型源代码/说明/报告；Game与大体积二进制、ZIP不提交。推送origin，创建对应GitHub Release、上传ZIP、设为最新稳定版；匿名下载验证哈希及公开状态，保留旧版。GitHub正文必须同步简明TXT内容与地址，不回填工程细节。
7. README记录技术方案、内部验证证据、真实限制、补丁名称、支持版本及公开链接，提交并推送。玩家说明简短不等于删除工程记录。

## 安装配置

`--installer-config` JSON包括`exe`、`exe_sha256`、`steam_app_id`、`originals:[{path,sha256}]`及`legacy:[{path,sha256}]`。originals只列会覆盖的原文件；legacy列已发布版本对应载荷哈希，避免将旧中文资源当作原件。新装自动保存原件，升级保留原件，卸载自动恢复；从无原件的旧包升级则自动交Steam校验恢复。不可分发原始游戏文件作备份。只适用于经过该游戏验证的文件替换方案；需要其他引擎动作时写对应适配器。
