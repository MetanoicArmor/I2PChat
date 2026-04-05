# -*- mode: python ; coding: utf-8 -*-
"""Slim PyInstaller bundle: Textual TUI only (no PyQt6 / qasync).

Release TUI zips use this output under dist/I2PChat-tui/.
The main I2PChat.spec still produces dist/I2PChat/ for GUI + in-.app TUI (shared Qt onedir).
"""
import glob
import os
import sys

_SPECDIR = os.path.dirname(os.path.abspath(SPEC))

_i2plib_modules = [
    os.path.splitext(os.path.relpath(f, 'vendor'))[0].replace(os.sep, '.')
    for f in glob.glob('vendor/i2plib/**/*.py', recursive=True)
    if not f.endswith('__init__.py')
]

_icon_file = 'i2pchat.ico' if sys.platform == 'win32' else 'icon.png'

_i2pd_binaries = []
if sys.platform == 'darwin':
    _i2pd_path = os.path.join('vendor', 'i2pd', 'darwin-arm64', 'i2pd')
    if os.path.isfile(_i2pd_path):
        _i2pd_binaries.append((_i2pd_path, os.path.join('vendor', 'i2pd', 'darwin-arm64')))
elif sys.platform == 'win32':
    _i2pd_path = os.path.join('vendor', 'i2pd', 'windows-x64', 'i2pd.exe')
    if os.path.isfile(_i2pd_path):
        _i2pd_binaries.append((_i2pd_path, os.path.join('vendor', 'i2pd', 'windows-x64')))
    _i2pd_binaries.extend(
        (dll, os.path.join('vendor', 'i2pd', 'windows-x64'))
        for dll in glob.glob(os.path.join('vendor', 'i2pd', 'windows-x64', '*.dll'))
    )
else:
    import platform

    _mach = platform.machine()
    if _mach in ('aarch64', 'arm64'):
        _i2pd_sub = 'linux-aarch64'
    else:
        _i2pd_sub = 'linux-x86_64'
    _i2pd_path = os.path.join('vendor', 'i2pd', _i2pd_sub, 'i2pd')
    if os.path.isfile(_i2pd_path):
        _i2pd_binaries.append((_i2pd_path, os.path.join('vendor', 'i2pd', _i2pd_sub)))

if os.environ.get("I2PCHAT_OMIT_BUNDLED_I2PD", "").strip().lower() in ("1", "true", "yes"):
    _i2pd_binaries = []

_tui_datas = [
    ('VERSION', '.'),
    ('i2pchat/blindbox/blindbox_server_example.py', 'i2pchat/blindbox'),
    ('i2pchat/blindbox/blindbox_service_standalone.py', 'i2pchat/blindbox'),
    ('i2pchat/blindbox/fail2ban', 'i2pchat/blindbox/fail2ban'),
    ('i2pchat/blindbox/daemon', 'i2pchat/blindbox/daemon'),
]

# Dependency graph from run_tui → chat_python covers i2pchat; do not mirror I2PChat.spec's
# full i2pchat/**/*.py hiddenimports (that forces main_qt and pulls Qt into TUI zips).
_hiddenimports = _i2plib_modules + [
    'rich',
    'textual',
    'pyperclip',
    'cffi',
    '_cffi_backend',
    'nacl',
    'nacl.secret',
    'nacl.public',
    'nacl.signing',
    'nacl.encoding',
    'nacl.exceptions',
    'PIL',
    'PIL.Image',
]

_qt_excludes = [
    'PyQt6',
    'PyQt6.QtCore',
    'PyQt6.QtGui',
    'PyQt6.QtWidgets',
    'PyQt6.QtMultimedia',
    'PyQt6.QtOpenGL',
    'PyQt6.QtPrintSupport',
    'PyQt6.sip',
    'PyQt6.QtSvg',
    'qasync',
    'PySide6',
    'PySide2',
    'shiboken6',
    'shiboken2',
]

_pathex = [os.path.join(_SPECDIR, 'vendor')]

a = Analysis(
    ['i2pchat/run_tui.py'],
    pathex=_pathex,
    binaries=_i2pd_binaries,
    datas=_tui_datas,
    hiddenimports=_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=_qt_excludes,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

_exe_common = dict(
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=[_icon_file],
)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='I2PChat-tui',
    console=True,
    **_exe_common,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='I2PChat-tui',
)
