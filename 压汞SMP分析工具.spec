# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

# Statistics endpoint (no secret); git-ignored, copied from telemetry_config.example.json.
telemetry_datas = [('telemetry_config.json', '.')] if Path('telemetry_config.json').is_file() else []

a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=[],
    datas=[('.\\assets\\mip-dragon-science-logo.ico', 'assets'),
           ('.\\assets\\dragonscience-logo.png', 'assets')] + telemetry_datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['PySide6', 'PySide2', 'PyQt6', 'torch', 'matplotlib', 'IPython', 'pytest', 'sphinx', 'tkinter'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='MIP综合分析-DragonScience',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['assets\\mip-dragon-science-logo.ico'],
)
