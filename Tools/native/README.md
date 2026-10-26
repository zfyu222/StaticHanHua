# 原生显示适配构建依赖

Avadon 的离线显示适配源文件位于 `Projects/avadon-the-black-fortress/Repack/`。
依赖 Microsoft Visual Studio 的 x86 MSVC 工具链；MinHook 源码下载到本目录 `MinHook/`，固定官方 v1.3.4 提交 `c3fcafdc10146beb5919319d0683e44e3c30d537`。

官方来源：https://github.com/TsudaKageyu/minhook

构建程序校验依赖提交、游戏 EXE、正式翻译及图片哈希。MinHook 静态链接并附 BSD 许可；不复制 Windows 系统 DLL。依赖 checkout、构建目录、二进制载荷和原件备份不入库。运行自建组件测试不等于启动游戏或实机验收。
