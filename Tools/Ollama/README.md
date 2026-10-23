# 本地视觉模型运行环境

便携版 Ollama v0.40.9，来自[官方 Windows 发布包](https://github.com/ollama/ollama/releases/tag/v0.40.9)。`setup.ps1`下载、解压到本目录，不安装桌面程序、不加入系统启动项。

`runtime/`、`models/`、`downloads/`、`logs/`均不入库。模型为 `qwen3-vl:8b-instruct-q4_K_M`，通过 image_text 的 `--pull` 下载；推理由工具拥有的隐藏子进程提供，限本机127.0.0.1:11435，并关闭云模型功能。任务结束关闭该服务并卸载模型。不接管同端口已有服务。

使用方法见[图片文字工具](../image_text/README.md)。OCR 在独立进程完成并退出后，才加载 Qwen；首次模型下载不执行推理。
