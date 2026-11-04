"""packaging/icon.png(2048x2048、透過)から、各所のアイコンを作る。
    python packaging/make_icons.py
Pillow が要る(python -m pip install pillow)。作るもの:
  packaging/icon.ico   Windows の exe・窓・タスクバー(16〜256)
  packaging/icon.icns  macOS の .app(Pillow で書き出す。iconutil は不要)
  static/favicon-32.png, static/favicon-180.png   ブラウザのタブと apple-touch-icon"""
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent
STATIC = HERE.parent / "static"
ICO_SIZES = [16, 24, 32, 48, 64, 128, 256]
ICNS_SIZES = [16, 32, 64, 128, 256, 512, 1024]


def main():
    src = Image.open(HERE / "icon.png").convert("RGBA")

    def at(size):
        return src.resize((size, size), Image.LANCZOS)

    at(256).save(HERE / "icon.ico", sizes=[(s, s) for s in ICO_SIZES])
    at(1024).save(HERE / "icon.icns", sizes=[(s, s) for s in ICNS_SIZES])
    at(32).save(STATIC / "favicon-32.png")
    at(180).save(STATIC / "favicon-180.png")
    print("icon.ico / icon.icns / favicon-32.png / favicon-180.png")


if __name__ == "__main__":
    main()
