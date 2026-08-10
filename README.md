# 🎬 dyTextExtractor · 抖音文案提取器

> 粘贴一个抖音分享链接，自动下载**无水印视频**、提取**音频**，用 **豆包 Seed-ASR** 识别**口播文案**，再用 **DeepSeek** 一键 **AI 排版**。

[![Python](https://img.shields.io/badge/Python-3.10+-blue?logo=python&logoColor=white)](https://www.python.org/)
[![License](https://img.shields.io/github/license/zerozhh/dyTextExtractor)](LICENSE)
[![Last Commit](https://img.shields.io/github/last-commit/zerozhh/dyTextExtractor)](https://github.com/zerozhh/dyTextExtractor/commits)
[![Stars](https://img.shields.io/github/stars/zerozhh/dyTextExtractor)](https://github.com/zerozhh/dyTextExtractor)

## ✨ 功能

- 🎬 **无水印视频** — 解析分享链接，下载高清无水印原视频
- 🎙️ **音频提取** — FFmpeg 一键抽取音频轨道
- 📝 **豆包 Seed-ASR** — 自动转写口播文案，带标点与数字归一化
- 🎞️ **SRT 字幕** — 识别同时产出带时间轴的逐句字幕（`subtitles_{语言}.srt`），可直接导入剪映 / PR / 字幕工具
- ✨ **AI 排版** — DeepSeek V4 Flash 整理文案（自动分段、修标点、去口语填充词；只排版不改写），结果本地缓存，同文案再次排版秒回、不重复计费
- ⚡ **渐进式展示** — 视频/音频一就绪立即在前端播放，无需等待识别完成
- 💾 **历史缓存** — 同一视频重复提取秒回结果，支持断点续跑；文案按识别语言分文件缓存，中/英各取所需不互相覆盖
- 🔗 **零网络复访** — 分享链接解析结果本地缓存，重复链接秒回且不依赖抖音网页（平台改版也不拖垮历史记录）
- 🌐 **WebUI + CLI 双入口**

## 🚀 快速开始

**环境要求**：Python 3.10+ · [uv](https://docs.astral.sh/uv/) · FFmpeg（`brew install ffmpeg`）· 豆包 API Key（必需）· DeepSeek API Key（可选）

```bash
# 安装
git clone https://github.com/zerozhh/dyTextExtractor.git
cd dyTextExtractor
uv sync

# 配置密钥（.env 每项都有注释说明）
cp .env.example .env
# 编辑 .env，至少填入 DOUBAO_API_KEY；AI 排版可选填 DEEPSEEK_API_KEY

# 启动 WebUI（默认监听 0.0.0.0，局域网内其他设备可访问）
uv run python web/app.py
# 本机打开 http://localhost:8080
# 同局域网设备打开 http://<本机IP>:8080（如 http://192.168.1.210:8080）
# 只想本机访问时：uv run python web/app.py 127.0.0.1
```

命令行方式：

```bash
uv run python main.py                        # 交互式粘贴链接
uv run python main.py "https://v.douyin.com/xxxxx"   # 直接传链接
```

## 📖 使用

1. 复制抖音分享链接，点「📋 粘贴」填入
2. 点「提取文案」→ 左栏视频/音频就绪即可播放
3. 识别完成后，点「✨ AI 排版」整理文案，可「复制」/「下载」

产物保存到 `output/{视频ID}/`：`video.mp4` · `audio.mp3` · `transcript_{识别语言}.md` · `subtitles_{识别语言}.srt` · `formatted_{识别语言}.md`（如 `transcript_auto.md`、`subtitles_zh-CN.srt`、`formatted_zh-CN.md`；语言功能前的旧文件 `transcript.md` 在默认识别时仍可命中）。`output/.url_index.json` 记录链接→视频映射，重复链接提取零网络解析。

## ❓ 常见问题

- **报错 `45000030`？** 未开通「录音文件识别」，到[火山引擎控制台](https://console.volcengine.com/speech/new/)开通后重试（前端有直达链接）
- **看不到新功能？** 浏览器缓存了旧页面，`Cmd + Shift + R` 硬刷新
- **FFmpeg 相关报错？** `brew install ffmpeg` 后重启服务

## ⚠️ 免责声明

仅供学习与研究，请遵守相关法律法规及平台规则。解析依赖抖音网页结构，平台改版后可能失效。

## 🤝 贡献

欢迎 PR 与 Issue：Fork → 修改 → Pull Request，较大改动建议先开 Issue 讨论。

## 🙏 致谢

[yzfly/douyin-mcp-server](https://github.com/yzfly/douyin-mcp-server) — 解析逻辑基于该项目改编（Apache-2.0，见 [LICENSE-APACHE](LICENSE-APACHE)）

## 📄 License

主体代码 [MIT](LICENSE) © 2026 包子 · `dy_extractor/douyin.py` 为 [Apache-2.0](LICENSE-APACHE)
