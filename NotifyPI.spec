# -*- mode: python ; coding: utf-8 -*-

import sys

sys.path.append(SPECPATH)

from _version import __version__

APP_VERSION = __version__
APP_BUILD_NUM = __version__.replace('.', '')

block_cipher = None


a = Analysis(
    ['pi_app.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('media', 'media')
    ],
    hiddenimports=[
        'PySide6.QtSvg',
        'PySide6.QtMultimedia'
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    exclude_binaries=True,
    name='NotifyPI',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='NotifyPI',
)
app = BUNDLE(
    coll,
    name='NotifyPI.app',
    icon='media/pi-update.png',
    bundle_identifier='com.dadoulab.NotifyPI',
    info_plist={
        # 添加一些元数据到 Info.plist
        'CFBundleName': 'NotifyPI',
        'CFBundleDisplayName': 'NotifyPI',
        'CFBundleExecutable': 'NotifyPI',
        'CFBundleShortVersionString': APP_VERSION,
        'CFBundleVersion': APP_BUILD_NUM,
        'NSHighResolutionCapable': 'True', # 支持高清屏
        'LSUIElement': 'True', # 将应用作为“代理应用”运行，不在 Dock 栏显示图标
    }
)
