# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for Windows .exe
from pathlib import Path

block_cipher = None
bridge_version = Path('../VERSION').read_text().strip()

from PyInstaller.utils.hooks import collect_all
ctk_datas, ctk_binaries, ctk_hidden = collect_all('customtkinter')

# Playwright bringt einen Node-Treiber als DATEN mit, nicht nur Python-Module —
# `hiddenimports` allein wuerde ihn nicht einpacken, und die Browser-Steuerung
# waere im ausgelieferten Programm tot.
try:
    pw_datas, pw_binaries, pw_hidden = collect_all('playwright')
except Exception:
    pw_datas, pw_binaries, pw_hidden = [], [], []

a = Analysis(
    ['tray_app.py'],
    pathex=['.'],
    binaries=ctk_binaries + pw_binaries,
    datas=[('bridge.py', '.'), ('_version.py', '.')] + ctk_datas + pw_datas,
    hiddenimports=[
        'pystray',
        'pystray._win32',
        'pyautogui',
        'PIL',
        'PIL.Image',
        'PIL.ImageDraw',
        'pyscreeze',
        'websockets',
        'edge_tts',
        'edge_tts.communicate',
        'aiohttp',
        'tkinter',
        'tkinter.ttk',
        'tkinter.filedialog',
        'customtkinter',
        # UI Automation: ohne diese Module im Paket kann die gebaute Windows-Bridge
        # keine Elemente finden — sie koennte nur klicken, wohin jemand zeigt.
        'uiautomation',
        'comtypes',
        'comtypes.client',
        'comtypes.stream',
        # Fehlten bisher — Replay-Modus und Mikrofon waren im ausgelieferten
        # Programm still tot, weil der Import abgefangen wird.
        'pynput',
        'pynput.mouse',
        'pynput.keyboard',
        'sounddevice',
        'numpy',
    ] + ctk_hidden + pw_hidden,
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
