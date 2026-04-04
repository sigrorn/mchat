# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec file for mchat

import os
import sys

block_cipher = None

a = Analysis(
    ['src/mchat/main.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('src/mchat/resources/icon.png', 'mchat/resources'),
        ('src/mchat/resources/icon.ico', 'mchat/resources'),
    ],
    hiddenimports=[
        'mchat',
        'mchat.config',
        'mchat.db',
        'mchat.main',
        'mchat.models',
        'mchat.models.message',
        'mchat.models.conversation',
        'mchat.pricing',
        'mchat.router',
        'mchat.providers',
        'mchat.providers.base',
        'mchat.providers.claude',
        'mchat.providers.openai_provider',
        'mchat.providers.gemini_provider',
        'mchat.providers.perplexity_provider',
        'mchat.workers',
        'mchat.workers.stream_worker',
        'mchat.ui',
        'mchat.ui.main_window',
        'mchat.ui.chat_widget',
        'mchat.ui.input_widget',
        'mchat.ui.sidebar',
        'mchat.ui.settings_dialog',
        'mchat.ui.commands',
        'markdown',
        'markdown.extensions.tables',
        'markdown.extensions.fenced_code',
        'markdown.extensions.sane_lists',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Unused Qt modules — mchat only needs QtWidgets + QtGui + QtCore
        'PySide6.QtWebEngine',
        'PySide6.QtWebEngineCore',
        'PySide6.QtWebEngineWidgets',
        'PySide6.Qt3DCore',
        'PySide6.Qt3DRender',
        'PySide6.Qt3DInput',
        'PySide6.Qt3DLogic',
        'PySide6.Qt3DExtras',
        'PySide6.Qt3DAnimation',
        'PySide6.QtMultimedia',
        'PySide6.QtMultimediaWidgets',
        'PySide6.QtQml',
        'PySide6.QtQuick',
        'PySide6.QtQuickWidgets',
        'PySide6.QtBluetooth',
        'PySide6.QtPositioning',
        'PySide6.QtSensors',
        'PySide6.QtSerialPort',
        'PySide6.QtRemoteObjects',
        'PySide6.QtCharts',
        'PySide6.QtDataVisualization',
        'PySide6.QtOpenGL',
        'PySide6.QtOpenGLWidgets',
        'PySide6.QtPdf',
        'PySide6.QtPdfWidgets',
        'PySide6.QtSvg',
        'PySide6.QtSvgWidgets',
        'PySide6.QtTest',
        'PySide6.QtXml',
        'PySide6.QtDesigner',
        'PySide6.QtHelp',
        'PySide6.QtNfc',
        'PySide6.QtDBus',
        'PySide6.QtNetworkAuth',
        'PySide6.QtSpatialAudio',
        'PySide6.QtHttpServer',
        'PySide6.QtLocation',
        'PySide6.QtWebChannel',
        'PySide6.QtWebSockets',
        'PySide6.QtScxml',
        'PySide6.QtStateMachine',
        'PySide6.QtVirtualKeyboard',
        'PySide6.QtAsyncio',
    ],
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
    name='mchat',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # skip UPX compression — faster builds
    console=False,  # windowed mode — no console
    icon='src/mchat/resources/icon.ico',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,  # skip UPX — faster builds, slightly larger output
    upx_exclude=[],
    name='mchat',
)
