# -*- mode: python ; coding: utf-8 -*-
import glob, os, sys

_local_modules = [
    os.path.splitext(f)[0]
    for f in glob.glob('*.py')
    if f != 'main_qt.py'
] + [
    os.path.splitext(os.path.relpath(f, '.'))[0].replace(os.sep, '.')
    for f in glob.glob('i2pchat/**/*.py', recursive=True)
    if not f.endswith('__init__.py')
]

_icon_file = 'i2pchat.ico' if sys.platform == 'win32' else 'icon.png'

a = Analysis(
    ['main_qt.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('VERSION', '.'),
        ('sun.max.png', '.'),
        ('moon.png', '.'),
        ('assets/sounds/notify.wav', 'assets/sounds'),
    ],
    hiddenimports=_local_modules + [
        'rich', 'textual', 'pyperclip',
        'cffi', '_cffi_backend',
        'nacl', 'nacl.secret', 'nacl.public', 'nacl.signing', 'nacl.encoding', 'nacl.exceptions',
    ],
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
    icon=[_icon_file],
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
