# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for macOS .app bundle
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

block_cipher = None
bridge_version = Path('../VERSION').read_text().strip()

# Playwright bringt einen Node-Treiber als DATEN mit, nicht nur Python-Module —
# `hiddenimports` allein wuerde ihn nicht einpacken, und die Browser-Steuerung
# waere im ausgelieferten Programm tot. Faellt die Abhaengigkeit (noch) nicht
# vorhanden aus, bleibt der Build trotzdem gruen: die Bridge meldet dann zur
# Laufzeit sauber, dass Playwright fehlt.
try:
    pw_datas, pw_binaries, pw_hidden = collect_all('playwright')
except Exception:
    pw_datas, pw_binaries, pw_hidden = [], [], []

a = Analysis(
    ['tray_app.py'],
    pathex=['.'],
    binaries=pw_binaries,
    datas=[
        ('bridge.py', '.'),   # bundle bridge.py next to executable
        ('_version.py', '.'),
    ] + pw_datas,
    hiddenimports=[
        'rumps',
        'pyautogui',
        'PIL',
        'PIL.Image',
        'PIL.ImageDraw',
        'pyscreeze',
        'websockets',
        'aiohttp',
        'ApplicationServices',
        'AVFoundation',
        'AppKit',
        'Quartz',
        'tkinter',
        # Fehlten bisher — Replay-Modus und Mikrofon waren im ausgelieferten
        # Programm still tot, weil der Import abgefangen wird.
        'pynput',
        'pynput.mouse',
        'pynput.keyboard',
        'sounddevice',
        'numpy',
    ] + pw_hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='AI-Employee Bridge',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='AI-Employee Bridge',
)

app = BUNDLE(
    coll,
    name='AI-Employee Bridge.app',
    icon=None,
    bundle_identifier='com.ai-employee.bridge',
    info_plist={
        'NSPrincipalClass': 'NSApplication',
        'NSAppleScriptEnabled': False,
        'LSUIElement': True,           # hide from Dock (menu bar only)
        'NSHighResolutionCapable': True,
        'CFBundleShortVersionString': bridge_version,
        'CFBundleVersion': bridge_version,
        'CFBundleName': 'AI-Employee Bridge',
        'NSAccessibilityUsageDescription': 'Required for desktop automation (click, type, read UI elements)',
        'NSScreenCaptureUsageDescription': 'Required to take screenshots for the AI agent',
        'NSMicrophoneUsageDescription': 'Required for voice mode so you can speak with your AI agent',
    },
)
