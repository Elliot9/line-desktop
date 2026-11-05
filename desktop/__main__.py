import argparse
import sys


def main():
    parser = argparse.ArgumentParser(description="LINE GTK 桌面程式")
    parser.add_argument("--demo", action="store_true", help="離線展示，不連接 LINE")
    parser.add_argument("--smoke-test", metavar="OUTPUT_DIR", help="操作真實 GTK 元件並儲存測試結果")
    options = parser.parse_args()
    try:
        from .app import Application
    except (ImportError, ValueError) as error:
        print("需要 Linux 系統套件 python3-gi、gir1.2-gtk-4.0。請以系統 /usr/bin/python3 執行。", file=sys.stderr)
        return 1
    return Application(options).run([sys.argv[0]])


if __name__ == "__main__":
    raise SystemExit(main())
