"""Microsoft Store 版(MSIX)を作る。通常版のビルド(Kataribe.spec)とは別。

  python packaging/build_store.py

1. speech.cpp と audio.cpp(Windows・Vulkan 版)を GitHub の最新リリースから取り、SHA-256 を照合して build/store-engine/ に置く
2. PyInstaller(Kataribe-store.spec)で dist/KataribeStore/ を作る
3. AppxManifest.xml とロゴを足し、makeappx で dist/Kataribe-<版>-store.msix を作る

Store に出す前に、Partner Center でアプリ名を予約し、「製品 ID」のページの値を packaging/store_identity.json に入れる
(name = Package/Identity/Name、publisher = Package/Identity/Publisher、publisher_display = PublisherDisplayName)。
署名は Store が行うので、ここではしない。"""
import hashlib
import json
import re
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
ENGINE = REPO / "build" / "store-engine"
OUT = REPO / "dist" / "KataribeStore"
LOGOS = {"StoreLogo.png": (50, 50), "Square44x44Logo.png": (44, 44), "Square44x44Logo.targetsize-256_altform-unplated.png": (256, 256),
         "Square150x150Logo.png": (150, 150), "Wide310x150Logo.png": (310, 150)}

MANIFEST = """<?xml version="1.0" encoding="utf-8"?>
<Package xmlns="http://schemas.microsoft.com/appx/manifest/foundation/windows10"
         xmlns:uap="http://schemas.microsoft.com/appx/manifest/uap/windows10"
         xmlns:rescap="http://schemas.microsoft.com/appx/manifest/foundation/windows10/restrictedcapabilities"
         IgnorableNamespaces="uap rescap">
  <Identity Name="{name}" Publisher="{publisher}" Version="{version}" ProcessorArchitecture="x64" />
  <Properties>
    <DisplayName>Kataribe</DisplayName>
    <PublisherDisplayName>{publisher_display}</PublisherDisplayName>
    <Logo>Assets\\StoreLogo.png</Logo>
  </Properties>
  <Dependencies>
    <TargetDeviceFamily Name="Windows.Desktop" MinVersion="10.0.17763.0" MaxVersionTested="10.0.26100.0" />
  </Dependencies>
  <Resources>
    <Resource Language="ja-JP" />
    <Resource Language="en-US" />
    <Resource Language="zh-CN" />
    <Resource Language="zh-TW" />
  </Resources>
  <Applications>
    <Application Id="Kataribe" Executable="Kataribe.exe" EntryPoint="Windows.FullTrustApplication">
      <uap:VisualElements DisplayName="Kataribe" Description="Kataribe" BackgroundColor="transparent"
                          Square150x150Logo="Assets\\Square150x150Logo.png" Square44x44Logo="Assets\\Square44x44Logo.png">
        <uap:DefaultTile Wide310x150Logo="Assets\\Wide310x150Logo.png" />
      </uap:VisualElements>
    </Application>
  </Applications>
  <Capabilities>
    <Capability Name="internetClient" />
    <rescap:Capability Name="runFullTrust" />
  </Capabilities>
</Package>
"""


def _get(url):
    return urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Kataribe"}), timeout=120)


def fetch(repo, suffix, folder, skip=()):
    """repo の最新リリースから、名前が suffix で終わる zip を取り、SHA-256 を照合して build/store-engine/<folder>/ に展開する。"""
    with _get(f"https://api.github.com/repos/{repo}/releases/latest") as res:
        release = json.loads(res.read())
    asset = next(a for a in release["assets"] if a["name"].endswith(suffix))
    dest, mark = ENGINE / folder, ENGINE / folder / "VERSION"
    if mark.is_file() and mark.read_text(encoding="utf-8") == release["tag_name"]:
        print(f"{repo} {release['tag_name']} (already there)")
        return
    print(f"{repo} {release['tag_name']}: downloading {asset['name']}")
    archive = REPO / "build" / asset["name"]
    archive.parent.mkdir(parents=True, exist_ok=True)
    with _get(asset["browser_download_url"]) as res, open(archive, "wb") as out:
        shutil.copyfileobj(res, out)
    want = (asset.get("digest") or "").removeprefix("sha256:")
    if not want:
        # digest の無いリリースは、同じリリースの SHA256SUMS で照合する
        sums = next((a for a in release["assets"] if a["name"] == "SHA256SUMS"), None)
        if sums:
            with _get(sums["browser_download_url"]) as res:
                want = next((ln.split()[0] for ln in res.read().decode().splitlines() if ln.strip().endswith(asset["name"])), "")
    if not want or hashlib.sha256(archive.read_bytes()).hexdigest() != want:
        archive.unlink()
        sys.exit(f"{repo}: SHA-256 does not match")
    shutil.rmtree(dest, ignore_errors=True)
    with zipfile.ZipFile(archive) as z:
        z.extractall(dest)
    archive.unlink()
    for name in skip:
        target = dest / name
        shutil.rmtree(target, ignore_errors=True) if target.is_dir() else target.unlink(missing_ok=True)
    mark.write_text(release["tag_name"], encoding="utf-8")


def fetch_engine():
    # 同梱するのは実行に要る物とライセンスだけ(開発用のヘッダーやコマンドライン版は入れない)
    fetch("nyosegawa/speech.cpp", "windows-x64-vulkan.zip", "speech", skip=("speech.h", "speech.lib"))
    fetch("0xShug0/audio.cpp", "bin-windows-x64-vulkan.zip", "audio", skip=("audiocpp_cli.exe", "audiocpp_gguf.exe", "tools"))
    # speech.cpp の zip にはライセンス文が入っていないので、リポジトリから取って添える(MIT は同梱が条件)
    with _get("https://raw.githubusercontent.com/nyosegawa/speech.cpp/main/LICENSE") as res:
        (ENGINE / "speech" / "LICENSE").write_bytes(res.read())


def makeappx():
    kits = Path(r"C:\Program Files (x86)\Windows Kits\10\bin")
    found = sorted(kits.glob("10.*/x64/makeappx.exe"))
    if not found:
        sys.exit("makeappx.exe was not found (install the Windows SDK)")
    return found[-1]


def main():
    version = re.search(r'VERSION = "(.+?)"', (REPO / "irodori" / "__init__.py").read_text(encoding="utf-8")).group(1)
    ident = {"name": "ADASoftware.Kataribe", "publisher": "CN=ADA Software", "publisher_display": "ADA Software"}
    saved = HERE / "store_identity.json"
    if saved.is_file():
        ident.update(json.loads(saved.read_text(encoding="utf-8")))
    else:
        print("packaging/store_identity.json is missing: using placeholder identity (fine for a local test, not for the Store)")
    fetch_engine()
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "Kataribe-store.spec"], cwd=REPO, check=True)
    assets = OUT / "Assets"
    assets.mkdir(exist_ok=True)
    icon = Image.open(HERE / "icon.png").convert("RGBA")
    for name, (w, h) in LOGOS.items():
        side = min(w, h)
        canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        canvas.paste(icon.resize((side, side), Image.LANCZOS), ((w - side) // 2, (h - side) // 2))
        canvas.save(assets / name)
    (OUT / "AppxManifest.xml").write_text(MANIFEST.format(version=f"{version}.0", **ident), encoding="utf-8")
    msix = REPO / "dist" / f"Kataribe-{version}-store.msix"
    msix.unlink(missing_ok=True)
    subprocess.run([str(makeappx()), "pack", "/o", "/d", str(OUT), "/p", str(msix)], check=True, stdout=subprocess.DEVNULL)
    print(f"done: {msix} ({msix.stat().st_size / 1e6:.0f} MB)")


if __name__ == "__main__":
    main()
