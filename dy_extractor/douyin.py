"""
解析原理：
1. 用移动端 UA 请求分享短链接，跟随重定向拿到视频 ID
2. 请求 https://www.iesdouyin.com/share/video/{video_id} 页面
3. 用正则抠出 window._ROUTER_DATA 里的 JSON，取出无水印视频地址
4. 把地址里的 playwm 换成 play 即为去水印版本

注意：该解析依赖抖音网页结构，抖音改版可能导致失效。
"""

import json
import re
from dataclasses import dataclass
from pathlib import Path

import requests

# 模拟移动端访问
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "EdgiOS/121.0.2277.107 Version/17.0 Mobile/15E148 Safari/604.1"
    )
}

# 提取分享链接的正则
_URL_RE = re.compile(
    r"http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\(\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+"
)
# 从页面中抠出 ROUTER_DATA
_ROUTER_DATA_RE = re.compile(r"window\._ROUTER_DATA\s*=\s*(.*?)</script>", re.DOTALL)
# 文件名中的非法字符
_ILLEGAL_CHARS = re.compile(r'[\\/:*?"<>|]')


@dataclass
class VideoInfo:
    """解析出的视频信息"""

    video_id: str
    title: str
    url: str  # 无水印视频直链


def parse_share_url(share_text: str) -> VideoInfo:
    """从分享文本中解析出无水印视频链接。"""
    urls = _URL_RE.findall(share_text)
    if not urls:
        raise ValueError("未找到有效的分享链接")

    share_url = urls[0]
    share_response = requests.get(share_url, headers=HEADERS, timeout=30)
    video_id = share_response.url.split("?")[0].strip("/").split("/")[-1]
    share_url = f"https://www.iesdouyin.com/share/video/{video_id}"

    response = requests.get(share_url, headers=HEADERS, timeout=30)
    response.raise_for_status()

    match = _ROUTER_DATA_RE.search(response.text)
    if not match or not match.group(1):
        raise ValueError("从HTML中解析视频信息失败")

    json_data = json.loads(match.group(1).strip())
    VIDEO_ID_PAGE_KEY = "video_(id)/page"
    NOTE_ID_PAGE_KEY = "note_(id)/page"

    if VIDEO_ID_PAGE_KEY in json_data["loaderData"]:
        original_video_info = json_data["loaderData"][VIDEO_ID_PAGE_KEY]["videoInfoRes"]
    elif NOTE_ID_PAGE_KEY in json_data["loaderData"]:
        original_video_info = json_data["loaderData"][NOTE_ID_PAGE_KEY]["videoInfoRes"]
    else:
        raise ValueError("无法从JSON中解析视频或图集信息")

    data = original_video_info["item_list"][0]

    # playwm -> play 即去水印
    video_url = data["video"]["play_addr"]["url_list"][0].replace("playwm", "play")
    desc = data.get("desc", "").strip() or f"douyin_{video_id}"
    desc = _ILLEGAL_CHARS.sub("_", desc)

    return VideoInfo(video_id=video_id, title=desc, url=video_url)


def download_video(video_info: VideoInfo, output_dir: Path, retries: int = 3) -> Path:
    """下载无水印视频到指定目录，返回文件路径。

    文件名统一用 video.mp4（与 WebUI 下载接口、README 约定一致）。

    完整性保证（第一性原理：下载产物必须完整，残缺文件绝不落盘）：
    1. 每次尝试前先清掉可能存在的半截文件；
    2. 写入字节数与响应头 Content-Length 比对，不一致视为下载不完整；
    3. 任何异常（网络中断、HTTP 错误、不完整）都会删除残文件并自动重试；
    4. 重试耗尽仍失败则抛错——绝不把残缺文件当成功结果返回。
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    filepath = output_dir / "video.mp4"

    for attempt in range(1, retries + 1):
        filepath.unlink(missing_ok=True)  # 清理上一次失败残留
        try:
            response = requests.get(video_info.url, headers=HEADERS, stream=True, timeout=60)
            response.raise_for_status()

            expected = response.headers.get("Content-Length")
            written = 0
            with open(filepath, "wb") as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        written += len(chunk)

            if expected is not None and written != int(expected):
                raise RuntimeError(f"下载不完整: 期望 {expected} 字节, 实际 {written} 字节")
            return filepath
        except Exception as e:
            filepath.unlink(missing_ok=True)
            if attempt == retries:
                raise RuntimeError(f"下载视频失败（已重试 {retries} 次）: {e}") from e
            print(f"⚠️ 下载失败（第 {attempt}/{retries} 次），正在重试: {e}")

    raise RuntimeError("download_video 异常退出")  # 不可达，仅兜底
