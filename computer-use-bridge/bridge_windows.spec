# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for Windows .exe

block_cipher = None

from PyInstaller.utils.hooks import collect_all
ctk_datas, ctk_binaries, ctk_hidden = collect_all('customtkinter')

a = Analysis(
    ['tray_app.py'],
    pathex=['.'],
    binaries=ctk_binaries,
    datas=[('bridge.py', '.')] + ctk_datas,
    hiddenimports=[
        'pystray',
        'pystray._win32',
        'pyautogui',
        'PIL',
        'PIL.Image',
        'PIL.ImageDraw',
        'pyscreeze',
        'websockets',
        'tkinter',
        'tkinter.ttk',
        'tkinter.filedialog',
        'customtkinter',
    ] + ctk_hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['rumps', 'AppKit', 'ApplicationServices', 'Quartz'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='AI-Employee-Bridge',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,          # no terminal window
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
    version=None,
    uac_admin=False,
)
