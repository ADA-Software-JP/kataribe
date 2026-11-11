# -*- mode: python ; coding: utf-8 -*-
# Microsoft Store 版の PyInstaller の設定(通常版は Kataribe.spec)。dist/KataribeStore/ を作る。
# 通常版との違い: 声を作る speech.cpp と audio.cpp を engine/ に同梱する、コンソールを出さない。
# 使い方: python packaging/build_store.py (audio.cpp の取得、このビルド、MSIX の作成までを行う)
import os

ENGINE = os.path.join(SPECPATH, "build", "store-engine")

a = Analysis(
    ['server.py'],
    pathex=[],
    binaries=[],
    datas=[('static', 'static'), ('packaging/icon.ico', 'packaging'), (ENGINE, 'engine')],
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
    console=False,
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
    name='KataribeStore',
)
