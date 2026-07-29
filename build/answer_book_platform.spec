import sys
import json
import subprocess
from pathlib import Path


ROOT = Path(SPECPATH).parent
FONT_ROOT = ROOT / "assets" / "fonts"
GENERATED_ROOT = ROOT / "build" / "generated"
GENERATED_ROOT.mkdir(parents=True, exist_ok=True)
APP_VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
try:
    revision = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    dirty = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=normal"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if dirty:
        revision = f"{revision}-dirty"
except Exception:
    revision = ""
build_manifest = GENERATED_ROOT / "RELEASE_MANIFEST.json"
build_manifest.write_text(
    json.dumps(
        {
            "package_name": "answer_book_platform_desktop",
            "version": APP_VERSION,
            "commit": revision,
            "build_platform": sys.platform,
        },
        ensure_ascii=False,
        indent=2,
    )
    + "\n",
    encoding="utf-8",
)

datas = [
    (str(ROOT / "web"), "web"),
    (str(ROOT / "config" / "providers.example.json"), "config"),
    (str(ROOT / "VERSION"), "."),
    (str(build_manifest), "."),
    (str(FONT_ROOT / "FONT_LICENSES.md"), "assets/fonts"),
    (
        str(FONT_ROOT / "dolbydu-font" / "matplotlib-compatible"),
        "assets/fonts/dolbydu-font/matplotlib-compatible",
    ),
]

hiddenimports = [
    "docx",
    "lxml",
    "latex2mathml",
    "PIL",
    "matplotlib",
    "matplotlib.backends.backend_agg",
    "numpy",
    "webview",
]
if sys.platform == "win32":
    hiddenimports += ["win32com", "win32com.client", "pythoncom", "pywintypes"]

a = Analysis(
    [str(ROOT / "desktop_launcher.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="真题解析平台",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
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
    name="真题解析平台",
)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="真题解析平台.app",
        icon=None,
        bundle_identifier="cn.nepuliang.answer-book-platform",
        info_plist={
            "CFBundleDisplayName": "真题解析平台",
            "CFBundleName": "真题解析平台",
            "CFBundleShortVersionString": APP_VERSION,
            "CFBundleVersion": APP_VERSION,
            "NSHighResolutionCapable": True,
            "NSLocalNetworkUsageDescription": "用于在局域网内查看本机任务状态和运行日志。",
        },
    )
