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
    _i2pd_path = os.path.join('vendor', 'i2pd', 'linux-x86_64', 'i2pd')
    if os.path.isfile(_i2pd_path):
        _i2pd_binaries.append((_i2pd_path, os.path.join('vendor', 'i2pd', 'linux-x86_64')))

_analysis_datas = [
    ('VERSION', '.'),
    ('assets/sounds/notify.wav', 'assets/sounds'),
    ('i2pchat/gui/fluent_emoji', 'i2pchat/gui/fluent_emoji'),
    ('i2pchat/gui/icons', 'i2pchat/gui/icons'),
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


if sys.platform == 'win32':
    a = _analysis('i2pchat/run_gui.py')
    a_tui = _analysis('i2pchat/run_tui.py')
    pyz = PYZ(a.pure)
    pyz_tui = PYZ(a_tui.pure)
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
    exe_tui = EXE(
        pyz_tui,
        a_tui.scripts,
        [],
        exclude_binaries=True,
        name='I2PChat-tui',
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=True,
        console=True,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
        icon=[_icon_file],
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
else:
    a = _analysis('i2pchat/run_gui.py')
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
