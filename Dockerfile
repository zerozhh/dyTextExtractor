# dyTextExtractor 容器镜像
#
# 设计动机：
#   - 宿主机 brew 装的 ffmpeg 是 macOS 二进制，Linux 容器无法执行
#     （OrbStack 亦明确拒绝 Mach-O：Exec format error），所以容器内
#     ffmpeg 用 PyPI 的 imageio-ffmpeg 静态 Linux 二进制补齐（约 26MB，
#     走清华源），不 apt、不装系统包。
#   - venv 放 /opt/venv 而非 /app/.venv：compose 用 bind mount 挂宿主
#     项目目录时会整体覆盖 /app，宿主 .venv 是 macOS 的、容器内不可用。
#   - 源码仍 COPY 进镜像（脱离挂载可独立启动）；compose 部署时被宿主
#     目录覆盖，改动即时生效。
#
# 基础镜像默认走 DaoCloud 国内源；被墙或换源时可用 build-arg 覆盖：
#   docker build --build-arg BASE_IMAGE=python:3.12-slim .

ARG BASE_IMAGE=docker.m.daocloud.io/library/python:3.12-slim
FROM ${BASE_IMAGE}

# uv / pip 一律走清华 PyPI 源；uv 不自下 Python（镜像自带 3.12）
ENV PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
    UV_DEFAULT_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple \
    UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PATH=/opt/venv/bin:${PATH}

RUN pip install --no-cache-dir uv

WORKDIR /app

# 容器专用补装（不动 pyproject，遵守项目「不新增运行时依赖」约束）：
#   imageio-ffmpeg — 自带静态 Linux ffmpeg（含 libmp3lame），软链到 PATH 供 ffmpeg-python 调用
#   watchfiles     — uvicorn --reload 的文件监听后端
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project \
    && uv pip install --python /opt/venv/bin/python imageio-ffmpeg watchfiles \
    && ln -s "$(/opt/venv/bin/python -c 'import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())')" /usr/local/bin/ffmpeg \
    && ffmpeg -version | head -n 1

# 源码层放最后：改代码不动依赖缓存层；uv.lock 变更才会触发上方重建
COPY main.py ./
COPY dy_extractor ./dy_extractor
COPY web ./web

EXPOSE 8080

# 热重载只盯两个源码目录而非整个 /app：output/ 产物写入、宿主 .venv、
# .git 都在目录外，不会触发误重启。本地改代码 → 容器内进程自动重启。
CMD ["uvicorn", "web.app:app", "--host", "0.0.0.0", "--port", "8080", "--reload", "--reload-dir", "/app/dy_extractor", "--reload-dir", "/app/web"]
