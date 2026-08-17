# CLAUDE.md — dyTextExtractor 开发规范

> 本文件写给在此仓库工作的人类与 AI。不是 README 的复读，只记录从代码、git
> 历史、.env.example 里**推导不出来**的约束与事实。若与 README 冲突，以本文件为准。

## 项目定位
粘贴抖音分享链接 → 下载无水印视频 + 提取音频 → 豆包 Seed-ASR 识别口播文案 →
DeepSeek AI 排版。WebUI + CLI 双入口，产物存 `output/{视频ID}/`。

## 技术栈事实
- FastAPI + uvicorn（WebUI）+ Jinja2 模板；前端在 `web/templates` + `web/static`，
  原生 JS，**无构建步骤、无 SPA 路由**，跨页状态走 URL 参数 + localStorage。
- uv 管理依赖（pyproject.toml + uv.lock）；密钥一律走 `.env`（已 .gitignore），勿硬编码。
- 依赖系统级 FFmpeg（`brew install ffmpeg`）。

## 架构硬约束（最高优先级）
产物目录即缓存键，命名方案：
`video.mp4` · `audio.mp3` · `transcript_{语言}.md` · `subtitles_{语言}.srt` · `formatted_{语言}.md`
（语言形如 `auto` / `zh-CN` / `en-US`）
- **所有产物路径必须经 `manifest.resolve()`（dy_extractor/manifest.py）解析**：用户可改名，
  `.files.json` 记「逻辑名→实际名」。直接拼路径/直接操作磁盘名 = bug。
- 缓存键 = (video_id, 语言)，各语言文案互不覆盖；重复链接零网络解析（`.url_index.json`）。
- 缓存只在源视频完整时可信任：`media_is_complete()`（非空 ≠ 完整）。
- 改名/删除/写回逻辑名走 manifest 层：`rename_file` / `clear_logic_prefix` / `reset_logic_file`。

## 安全不变量
- 对外请求先过域名白名单：仅 `douyin.com` / `iesdouyin.com`（`_assert_douyin_host`）——SSRF 防护。
- 产物文件读写拒绝 symlink 与路径遍历。
- 前端渲染不可信文本：先 `esc()` 再格式化（`renderMarkdown`），链接仅 http/https/# 协议。

## 明确不做（边界）
1. 不兼容旧版本数据（legacy 兼容代码已删，2026-08 决策）。
2. 不引入前端构建步骤 / 不引 SPA 框架。
3. 不新增运行时依赖（当前 6 个，刻意最小化；确有需要须在 commit 说明）。
4. 不绕过 `manifest.resolve()` 直接操作产物文件。
5. 不硬编码密钥，一律走 `.env`。

## 运行与测试事实
- 启动 WebUI：`uv run python web/app.py`（默认 0.0.0.0:8080，局域网可访问）
  只本机：`uv run python web/app.py 127.0.0.1`（argv[1]=host，argv[2]=port）
- CLI：`uv run python main.py [分享链接]`（无链接则交互式粘贴）
- Docker：`docker compose up -d --build`（OrbStack；DaoCloud 镜像源 + 清华 PyPI 源）。
  容器内 ffmpeg 来自 PyPI `imageio-ffmpeg` 静态二进制（软链到 PATH），**不能**
  挂宿主 brew ffmpeg——macOS 二进制在 Linux 容器不可执行（OrbStack 同样拒绝）。
  注意 `imageio-ffmpeg` **只含 ffmpeg、不含 ffprobe**——`media.py` 的完整性
  校验因此只依赖 ffmpeg（`-c copy` 流拷贝等价于 ffprobe 逐包扫描），勿改回
  ffprobe（2026-08 踩坑：容器内无 ffprobe 被误判「校验不完整」，删完好视频）。
  `imageio-ffmpeg` / `watchfiles` 只装进镜像（Dockerfile 内 `uv pip install`），
  不进 pyproject，维持 6 依赖约束。venv 在镜像内 `/opt/venv`（非 `/app/.venv`，
  防挂载覆盖）。uvicorn `--reload` 只盯 `dy_extractor/` 与 `web/`，output/ 写入
  不触发重启；`WATCHFILES_FORCE_POLLING=1` 保证挂载盘上监听可靠。
- `tests/` 在 .gitignore 中，`.venv` 未装 pytest；本地验证用临时测试脚本，不上 CI。

## 外部服务知识
- 豆包语音双认证：新版控制台单头 `X-Api-Key`（DOUBAO_API_KEY）；旧版双头
  `X-Api-App-Key` + `X-Api-Access-Key`（DOUBAO_APP_ID + DOUBAO_ACCESS_TOKEN）。
  `config.get_api_key` 在双头已配时返回空串，`doubao_asr._headers` 择路。
- AI 排版走 DeepSeek（DEEPSEEK_API_KEY 可选），结果落盘缓存，同文案二次排版不重复调用。
- 识别语言 `DOUBAO_LANGUAGE`、资源 ID `DOUBAO_RESOURCE_ID` 均可在 .env 配置。

## 代码风格
- 注释与 commit 用中文；模块 docstring 先写「第一性原理」说明设计动机。
- commit 用 conventional 前缀 + 中文描述，按功能拆分提交（`feat:` / `fix:` / `refactor:`）。
- Python 3.10+，函数加类型标注；前端保持无依赖原生 JS。
