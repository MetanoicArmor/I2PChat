# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['main_qt.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=['rich', 'textual', 'pyperclip', 'crypto', 'nacl', 'nacl.secret', 'nacl.public', 'nacl.signing', 'nacl.encoding', 'nacl.exceptions'],
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
    name='I2PChat',
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
    icon=['icon-1024.png'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='I2PChat',
)
