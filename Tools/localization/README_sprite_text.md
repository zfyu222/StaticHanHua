# TGA 图片文字替换

`sprite_text.py` 提供 `replace_text(source, label, box, font_path, font_size=23, color=(239,224,174), erase_threshold=150, background_box=None)`，返回修改后的文件字节与校验记录。调用方负责保存到新路径，模块不覆盖原文件。

依赖：Pillow、NumPy、opencv-python-headless。支持无色表、未压缩的16/24/32位TGA。保留头部、原尺寸、方向、位深、透明位/通道及文字框外像素；拒绝不支持的格式。原图与输出通过Pillow解码后核对。

文字框由项目按原图审查指定。默认检测亮色笔画、扩展覆盖描边并用OpenCV修复背景；灰色禁用文字需降低erase_threshold。复杂背景可提供同一原图上的无字background_box补底。字体需支持目标字符；字串过长会自动缩小，仍无法容纳则报错。

程序检查只能证明格式和像素保护，不能证明旧字全部清除、背景自然或目标文字美观。必须逐张或通过图集做视觉审查，并在游戏内验收；勿未经检查直接批量部署。

项目调用示例：`Projects/inquisitor/build_menu_images.py`。调用后 `build_patch.py` 校验视觉审查状态和输出哈希，再打包进入独立资源覆盖包。
