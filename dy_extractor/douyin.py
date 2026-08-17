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

# SSRF 防护：解析阶段只允许请求抖音系域名，拒绝内网/回环/任意地址
_DOUYIN_HOSTS = ("douyin.com", "iesdouyin.com")


def _assert_douyin_host(url: str) -> None:
    """校验 URL 主机属于抖音系域名，否则抛 ValueError（SSRF 防护）。"""
    from urllib.parse import urlparse
    host = (urlparse(url).hostname or "").lower()
    if not host or not any(host == d or host.endswith("." + d) for d in _DOUYIN_HOSTS):
        raise ValueError("仅支持抖音分享链接")


@dataclass
class VideoInfo:
    """解析出的视频信息"""

    video_id: str
    title: str
    url: str  # 无水印视频直链


def extract_share_url(share_text: str) -> str | None:
    """从分享文本中提取第一个 URL；找不到则返回 None。

    纯本地正则，不联网——URL 索引（.url_index.json）的 lookup/record
    都依赖它把分享文本稳定地归一到同一个键。
    """
    urls = _URL_RE.findall(share_text or "")
    return urls[0] if urls else None


def parse_share_url(share_text: str) -> VideoInfo:
    """从分享文本中解析出无水印视频链接。"""
    share_url = extract_share_url(share_text)
    if not share_url:
        raise ValueError("未找到有效的分享链接")
    _assert_douyin_host(share_url)  # SSRF 防护：拒绝内网/回环/任意域名

    # 两跳共用 Session 跨跳保留 cookie：抖音分享页已校验 ttwid（2026-08 实测），
    # 无该 cookie 时返回只有风控元数据的空壳页（loaderData 无 videoInfoRes），
    # 表现为「出错了：videoInfoRes」
    session = requests.Session()
    session.headers.update(HEADERS)

    share_response = session.get(share_url, timeout=30)
    # 直接贴完整链接（douyin.com/video/...）时无短链跳转、第一跳不种 ttwid，
    # 预热 iesdouyin 主页补种；短链场景第一跳已自带，跳过不多花请求
    if "ttwid" not in session.cookies:
        try:
            session.get("https://www.iesdouyin.com/", timeout=30)
        except requests.RequestException:
            pass  # 预热失败不阻断，仍尝试分享页
    video_id = share_response.url.split("?")[0].strip("/").split("/")[-1]
    share_url = f"https://www.iesdouyin.com/share/video/{video_id}"

    response = session.get(share_url, timeout=30)
    response.raise_for_status()

    match = _ROUTER_DATA_RE.search(response.text)
    if not match or not match.group(1):
        raise ValueError("从HTML中解析视频信息失败")

    json_data = json.loads(match.group(1).strip())
    VIDEO_ID_PAGE_KEY = "video_(id)/page"
    NOTE_ID_PAGE_KEY = "note_(id)/page"

    if VIDEO_ID_PAGE_KEY in json_data["loaderData"]:
        page_data = json_data["loaderData"][VIDEO_ID_PAGE_KEY]
    elif NOTE_ID_PAGE_KEY in json_data["loaderData"]:
        page_data = json_data["loaderData"][NOTE_ID_PAGE_KEY]
    else:
        raise ValueError("无法从JSON中解析视频或图集信息")
    if "videoInfoRes" not in page_data:
        raise ValueError("分享页未返回视频数据（可能触发抖音风控），请稍后重试")
    original_video_info = page_data["videoInfoRes"]

    data = original_video_info["item_list"][0]

    # playwm -> play 即去水印
    video_url = data["video"]["play_addr"]["url_list"][0].replace("playwm", "play")
    desc = data.get("desc", "").strip() or f"douyin_{video_id}"
    desc = _ILLEGAL_CHARS.sub("_", desc)

    return VideoInfo(video_id=video_id, title=desc, url=video_url)


def download_video(video_info: VideoInfo, output_dir: Path, retries: int = 3, progress=None) -> Path:
    """下载无水印视频到指定目录，返回文件路径。

    文件名统一用 video.mp4（与 WebUI 下载接口、README 约定一致）。

    完整性保证（第一性原理：下载产物必须完整，残缺文件绝不落盘）：
    1. 每次尝试前先清掉可能存在的半截文件；
    2. 写入字节数与响应头 Content-Length 比对，不一致视为下载不完整；
    3. 任何异常（网络中断、HTTP 错误、不完整）都会删除残文件并自动重试；
    4. 重试耗尽仍失败则抛错——绝不把残缺文件当成功结果返回。

    progress: 可选回调 progress(written, total)，下载中定期触发（有 Content-Length
        时按每 1% 节流；未知总大小时按每 2MB 触发、total 传 None）。下载完成时补
        一次收尾回调，保证前端进度条走满。
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    filepath = output_dir / "video.mp4"

    for attempt in range(1, retries + 1):
        filepath.unlink(missing_ok=True)  # 清理上一次失败残留
        try:
            response = requests.get(video_info.url, headers=HEADERS, stream=True, timeout=60)
            response.raise_for_status()

            expected = response.headers.get("Content-Length")
            expected_int = int(expected) if expected else None
            written = 0
            last_reported = -1
            with open(filepath, "wb") as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        written += len(chunk)
                        if progress:
                            if expected_int:
                                # 有总大小：按百分比节流（每跨过 1% 回调一次）
                                pct = written * 100 // expected_int
                                if pct != last_reported:
                                    last_reported = pct
                                    progress(written, expected_int)
                            else:
                                # 无总大小（无 Content-Length）：每 2MB 回调一次
                                bucket = written // (2 * 1024 * 1024)
                                if bucket != last_reported:
                                    last_reported = bucket
                                    progress(written, None)

            if progress:
                # 收尾回调：保证前端进度条走满（total 未知时传 written，前端算成 100%）
                progress(written, expected_int or written)

            if expected_int is not None and written != expected_int:
                raise RuntimeError(f"下载不完整: 期望 {expected_int} 字节, 实际 {written} 字节")
            return filepath
        except Exception as e:
            filepath.unlink(missing_ok=True)
            if attempt == retries:
                raise RuntimeError(f"下载视频失败（已重试 {retries} 次）: {e}") from e
            print(f"⚠️ 下载失败（第 {attempt}/{retries} 次），正在重试: {e}")

    raise RuntimeError("download_video 异常退出")  # 不可达，仅兜底
