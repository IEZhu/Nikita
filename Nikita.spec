# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path


project_root = Path(SPECPATH)


a = Analysis(
    [str(project_root / 'Nikita.py')],
    pathex=[str(project_root)],
    binaries=[],
    datas=[(str(project_root / 'LICENSE'), '.')],
    hiddenimports=['subprocess', 'cherrypy', 'urllib', 'threading', 'requests', 're', 'time', 'operator', 'json', 'psutil', 'shlex', 'platform', 'socket', 'sqlite3', 'src.parser', 'src.reader', 'src.dictionaries', 'src.messenger', 'src.globals', 'src.tools', 'src.solr', 'src.sender', 'src.redis_manager', 'src.state_manager', 'src.parser_state', 'src.cherry', 'src.mcp', 'src.mcp_query', 'clickhouse_driver', 'win32timezone'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['numpy', 'cryptography', 'lib2to3', 'win32com', 'gevent', 'matplotlib', '__PyInstaller_hooks_0_pandas_io_formats_style'],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [('O', None, 'OPTION')],
    exclude_binaries=True,
    name='Nikita',
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
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='Nikita',
)
