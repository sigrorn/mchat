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
    name='mchat',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,  # windowed mode — no console
    icon='src/mchat/resources/icon.ico',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='mchat',
)
