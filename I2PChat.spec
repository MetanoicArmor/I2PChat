# -*- mode: python ; coding: utf-8 -*-
import glob, os, sys

from PyInstaller.building.datastruct import normalize_toc

_SPECDIR = os.path.dirname(os.path.abspath(SPEC))

_local_modules = [
    os.path.splitext(os.path.relpath(f, '.'))[0].replace(os.sep, '.')
    for f in glob.glob('i2pchat/**/*.py', recursive=True)
    if not f.endswith('__init__.py')
] + [
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

# Set I2PCHAT_OMIT_BUNDLED_I2PD=1 for a winget/store-friendly Windows build without embedded i2pd (Microsoft pipeline AV).
if os.environ.get("I2PCHAT_OMIT_BUNDLED_I2PD", "").strip().lower() in ("1", "true", "yes"):
    _i2pd_binaries = []

_analysis_datas = [
    ('VERSION', '.'),
    ('assets/sounds/notify.wav', 'assets/sounds'),
    ('i2pchat/gui/fluent_emoji', 'i2pchat/gui/fluent_emoji'),
    ('i2pchat/gui/icons', 'i2pchat/gui/icons'),
    ('i2pchat/gui/assets', 'i2pchat/gui/assets'),
    ('i2pchat/blindbox/blindbox_server_example.py', 'i2pchat/blindbox'),
    ('i2pchat/blindbox/blindbox_service_standalone.py', 'i2pchat/blindbox'),
    ('i2pchat/blindbox/fail2ban', 'i2pchat/blindbox/fail2ban'),
    ('i2pchat/blindbox/daemon', 'i2pchat/blindbox/daemon'),
]

_hiddenimports = _local_modules + [
    'rich', 'textual', 'pyperclip',
    'cffi', '_cffi_backend',
    'nacl', 'nacl.secret', 'nacl.public', 'nacl.signing', 'nacl.encoding', 'nacl.exceptions',
]

_pathex = [os.path.join(_SPECDIR, 'vendor')]


def _analysis(entry_script):
    return Analysis(
        [entry_script],
        pathex=_pathex,
        binaries=_i2pd_binaries,
        datas=_analysis_datas,
        hiddenimports=_hiddenimports,
        hookspath=[],
        hooksconfig={},
        runtime_hooks=[],
        excludes=[],
        noarchive=False,
        optimize=0,
    )


# GUI (windowed) + TUI (console) в одном onedir; имя TUI: I2PChat-tui (.exe на Windows).
a = _analysis('i2pchat/run_gui.py')
a_tui = _analysis('i2pchat/run_tui.py')
pyz = PYZ(a.pure)
pyz_tui = PYZ(a_tui.pure)

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
    name='I2PChat',
    console=False,
    **_exe_common,
)
exe_tui = EXE(
    pyz_tui,
    a_tui.scripts,
    [],
    exclude_binaries=True,
    name='I2PChat-tui',
    console=True,
    **_exe_common,
)
coll = COLLECT(
    exe,
    exe_tui,
    normalize_toc(a.binaries + a_tui.binaries),
    normalize_toc(a.datas + a_tui.datas),
    strip=False,
    upx=True,
    upx_exclude=[],
    name='I2PChat',
)
