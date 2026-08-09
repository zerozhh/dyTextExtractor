"""命令行入口：支持 `dy-extract "链接"` 或交互式粘贴链接。"""

import sys

from .pipeline import extract


def main() -> None:
    # 命令行参数模式：dy-extract "https://v.douyin.com/xxxx"
    if len(sys.argv) > 1:
        try:
            extract(sys.argv[1])
        except Exception as e:
            print(f"\n❌ 出错: {e}")
            sys.exit(1)
        return

    # 交互模式：粘贴链接，回车提取
    print("=" * 50)
    print("  dyTextExtractor - 抖音文案提取器")
    print("  粘贴抖音分享链接，回车开始提取（Ctrl+C 退出）")
    print("=" * 50)
    while True:
        try:
            link = input("\n分享链接 > ").strip()
            if not link:
                continue
            extract(link)
        except KeyboardInterrupt:
            print("\n再见 👋")
            break
        except Exception as e:
            print(f"\n❌ 出错: {e}")


if __name__ == "__main__":
    main()
