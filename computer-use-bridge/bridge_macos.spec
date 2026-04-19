# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for macOS .app bundle

block_cipher = None

a = Analysis(
    ['tray_app.py'],
    pathex=['.'],
    binaries=[],
    datas=[
        ('bridge.py', '.'),   # bundle bridge.py next to executable
    ],
    hiddenimports=[
        'rumps',
        'pyautogui',
        'PIL',
        'PIL.Image',
        'PIL.ImageDraw',
        'pyscreeze',
        'websockets',
        'ApplicationServices',
        'AppKit',
        'Quartz',
        'tkinter',
    ],
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
        'CFBundleShortVersionString': '1.0.0',
        'CFBundleName': 'AI-Employee Bridge',
        'NSAccessibilityUsageDescription': 'Required for desktop automation (click, type, read UI elements)',
        'NSScreenCaptureUsageDescription': 'Required to take screenshots for the AI agent',
    },
)
