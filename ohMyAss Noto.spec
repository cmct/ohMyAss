# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files
from PyInstaller.utils.hooks import collect_all

datas = [('ohMyAss Noto.ico', '.'), ('ohMyAss Noto.png', '.'),
         ('LICENSE', '.'), ('THIRD_PARTY_NOTICES.md', '.'),
         ('SOURCE.md', '.'),
         ('bin', 'bin'), ('bin64', 'bin64'), ('third_party', 'third_party')]
binaries = []
hiddenimports = ['atv_subs']
datas += collect_data_files('zhconv')
for package in ('tkinterdnd2', 'isubrip', 'httpx2'):
    tmp_ret = collect_all(package)
    datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['ohMyAss_Noto.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
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
    a.binaries,
    a.datas,
    [],
    name='ohMyAss Noto',
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
    version='version_info.txt',
    icon=['ohMyAss Noto.ico'],
)
