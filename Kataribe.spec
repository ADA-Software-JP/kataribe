# -*- mode: python ; coding: utf-8 -*-
# PyInstaller の設定。Windows は dist/Kataribe/(exe と _internal)、macOS は dist/Kataribe.app を作る。
# 使い方: python -m pip install pywebview pyinstaller; python -m PyInstaller --noconfirm --clean Kataribe.spec
# アイコンは packaging/make_icons.py で packaging/icon.png から作る(icon.ico は窓にも使うので同梱する)
import os
import re
import sys

IS_MAC = sys.platform == "darwin"
with open(os.path.join(SPECPATH, "irodori", "__init__.py"), encoding="utf-8") as f:
    VERSION = re.search(r'VERSION = "(.+?)"', f.read()).group(1)

a = Analysis(
    ['server.py'],
    pathex=[],
    binaries=[],
    datas=[('static', 'static'), ('packaging/icon.ico', 'packaging')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Kataribe',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    # Windows はコンソールに URL と止め方を出す。Mac は窓だけの .app にする
    console=not IS_MAC,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='packaging/icon.ico',
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='Kataribe',
)
if IS_MAC:
    app = BUNDLE(
        coll,
        name='Kataribe.app',
        icon='packaging/icon.icns',
        bundle_identifier=None,
        info_plist={'CFBundleShortVersionString': VERSION, 'NSHighResolutionCapable': True},
    )
