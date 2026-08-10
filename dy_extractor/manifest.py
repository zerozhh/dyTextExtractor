"""output/{video_id}/ 内文件名的逻辑层。

第一性原理：目录键 video_id 是机器寻址的稳定键；磁盘文件名是用户可自定义的
展示名。两者之间用 .files.json 解耦——「逻辑名」（系统约定名，如 video.mp4）
→「实际文件名」（默认同名，用户改名后不同）。

- resolve():        读路径统一入口（播放/下载/缓存命中/断点续跑都走这里）
- rename_file():    用户改名（校验 + 实际重命名 + 写 manifest；改名对新名=逻辑名
                    时视为「恢复默认名」，从 manifest 移除映射）
- reset_logic_file(): 系统重建某逻辑文件时调用，删除其映射确保写回逻辑名
- logic_name_for(): 按磁盘实际文件名反查逻辑名（历史页改名入口用）
"""

import json
import re
from pathlib import Path

MANIFEST_NAME = ".files.json"

# 可改名的逻辑文件（transcript/subtitles/formatted 依语言展开）
_LOGIC_RE = re.compile(
    r"^(video\.mp4|audio\.mp3|transcript(_[^/]+)?\.md|"
    r"subtitles(_[^/]+)?\.srt|formatted(_[^/]+)?\.md)$"
)

# 非法文件名字符：文件系统禁止的分隔符 + 控制字符
_ILLEGAL = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
MAX_NAME_LEN = 200          # 字符上限（共识约定）
MAX_NAME_BYTES = 200        # utf-8 字节上限（贴近文件系统 255 字节限制，留余量）


def _manifest_path(video_dir: Path) -> Path:
    return Path(video_dir) / MANIFEST_NAME


def read_manifest(video_dir: Path) -> dict:
    """读取 .files.json；缺失/损坏/非对象（如合法 JSON 但为 list）均视为空映射。"""
    try:
        data = json.loads(_manifest_path(video_dir).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_manifest(video_dir: Path, mapping: dict) -> None:
    """原子写回 .files.json（tmp + replace，避免半截文件）。

    映射为空时直接删除文件——没有改名记录就不需要 manifest，保持目录干净。
    """
    path = _manifest_path(video_dir)
    if not mapping:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(mapping, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def resolve(video_dir: Path, logic_name: str) -> Path:
    """返回逻辑名对应的实际磁盘路径（未改名则即逻辑名）。

    防御：manifest 被篡改/损坏时 actual 可能含分隔符或逃逸 video_dir（如
    "../.env"、绝对路径），此时回退逻辑名，保证返回值始终落在目录内——
    防止经 FileResponse 被读成任意文件。
    """
    actual = read_manifest(video_dir).get(logic_name, logic_name)
    if not actual or actual in (".", "..") or "/" in actual or "\\" in actual \
            or Path(actual).name != actual:
        actual = logic_name
    return Path(video_dir) / actual


def reset_logic_file(video_dir: Path, logic_name: str) -> None:
    """系统准备重建某逻辑文件时调用：删除其映射，确保后续写回逻辑名。"""
    mapping = read_manifest(video_dir)
    if logic_name in mapping:
        mapping.pop(logic_name)
        _write_manifest(video_dir, mapping)


def clear_logic_prefix(video_dir: Path, prefix: str) -> None:
    """删除所有以 prefix 开头的逻辑文件（含改名后的实际文件）并清映射。

    视频作废时派生产物必须整体作废。前缀 glob 只匹配逻辑名（transcript_*.md），
    匹配不到改名后的实际名（如"我的笔记.md"）——必须经 manifest 反查删除，
    否则基于损坏视频的过期派生内容会被当作缓存命中返回。
    """
    video_dir = Path(video_dir)
    mapping = read_manifest(video_dir)
    changed = False
    for logic in list(mapping):
        if logic.startswith(prefix):
            (video_dir / mapping[logic]).unlink(missing_ok=True)
            mapping.pop(logic)
            changed = True
    if changed:
        _write_manifest(video_dir, mapping)
    # 再删按逻辑名落盘的（未改名）文件
    for p in video_dir.glob(prefix + "*"):
        if p.is_file() and p.name != MANIFEST_NAME:
            p.unlink(missing_ok=True)


def logic_name_for(video_dir: Path, actual_name: str) -> str | None:
    """按磁盘实际文件名反查逻辑名；该文件不可改名返回 None。"""
    mapping = read_manifest(video_dir)
    for logic, actual in mapping.items():
        if actual == actual_name:
            return logic
    if _LOGIC_RE.match(actual_name):
        return actual_name
    return None


def validate_new_name(new_name: str) -> str | None:
    """校验新文件名，非法返回中文错误信息，合法返回 None。

    规则（共识第 8 条）：拦截非法字符、防首尾空格/点、长度上限。
    扩展名保留需对比逻辑名后缀，在 rename_file 中校验（本函数无该上下文）；
    目录内重名与源文件存在性也在 rename_file 中校验。
    """
    new_name = (new_name or "").strip()
    if not new_name:
        return "文件名不能为空"
    if len(new_name) > MAX_NAME_LEN or len(new_name.encode("utf-8")) > MAX_NAME_BYTES:
        return "文件名过长（最长 200 字符）"
    if new_name.startswith(".") or new_name.endswith(".") or new_name.endswith(" "):
        return "文件名不能以点或空格开头/结尾"
    if _ILLEGAL.search(new_name):
        return '文件名含非法字符（/ \\ : * ? " < > | 等）'
    return None


def rename_file(video_dir: Path, logic_name: str, new_name: str) -> Path:
    """改名：校验 + 实际重命名 + 更新 manifest，返回新路径。抛 ValueError 带中文原因。"""
    video_dir = Path(video_dir)
    err = validate_new_name(new_name)
    if err:
        raise ValueError(err)
    new_name = new_name.strip()

    # 扩展名必须保留：防止改坏产物导致播放/下载 MIME 错乱
    if Path(new_name).suffix != Path(logic_name).suffix:
        raise ValueError(f"扩展名必须保留（{Path(logic_name).suffix}）")

    # 不能改名为另一个系统逻辑名：防跨逻辑串味（如 transcript_auto.md →
    # formatted_auto.md 会被 /api/format 直接写路径覆盖）。仅允许「恢复默认名」。
    if new_name != logic_name and _LOGIC_RE.match(new_name):
        raise ValueError("不能改名为系统逻辑文件名")

    actual_path = resolve(video_dir, logic_name)
    if not actual_path.exists():
        raise ValueError("源文件不存在")

    # 目标不能与目录内其他产物重名（自己除外）
    target = video_dir / new_name
    if target.exists() and target != actual_path:
        raise ValueError("目录内已有同名文件")

    if target == actual_path:
        return actual_path  # 名字没变，无操作

    actual_path.rename(target)
    mapping = read_manifest(video_dir)
    if new_name == logic_name:
        mapping.pop(logic_name, None)  # 恢复默认名：移除映射
    else:
        mapping[logic_name] = new_name
    _write_manifest(video_dir, mapping)
    return target
