exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name="CapitalAnalytics",
    debug=False, bootloader_ignore_signals=False, strip=False,
    upx=False, console=False, disable_windowed_traceback=False,
    argv_emulation=False, target_arch=None,
    codesign_identity=None, entitlements_file=None,
)