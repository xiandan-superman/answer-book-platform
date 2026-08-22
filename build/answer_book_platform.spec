import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(SPECPATH).parent
FONT_ROOT = ROOT / "assets" / "fonts"
GENERATED_ROOT = ROOT / "build" / "generated"
GENERATED_ROOT.mkdir(parents=True, exist_ok=True)
APP_VERSION = (ROOT / "APP_VERSION").read_text(encoding="utf-8").strip()
PLATFORM_VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
try:
    revision = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
except Exception:
    revision = ""

build_manifest = GENERATED_ROOT / "RELEASE_MANIFEST.json"
build_manifest.write_text(
    json.dumps(
        {
            "package_name": "answer_book_platform_desktop",
            "product_name": "真题解析与生题平台",
            "version": PLATFORM_VERSION,
            "app_version": APP_VERSION,
            "commit": revision,
            "build_platform": sys.platform,
            "update": json.loads((ROOT / "config" / "update.json").read_text(encoding="utf-8")),
        },
        ensure_ascii=False,
        indent=2,
    )
    + "\n",
    encoding="utf-8",
)

support_config_source = ROOT / "config" / "support_reporting.json"
support_config_build = GENERATED_ROOT / "support_reporting.json"
if support_config_source.exists():
    support_config = json.loads(support_config_source.read_text(encoding="utf-8"))
else:
    support_config = {
        "receiver_url": os.environ.get("ANSWER_BOOK_SUPPORT_URL", ""),
        "receiver_token": os.environ.get("ANSWER_BOOK_SUPPORT_TOKEN", ""),
    }
support_config_build.write_text(json.dumps(support_config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

hybrid_config = json.loads((ROOT / "config" / "hybrid_cloud.example.json").read_text(encoding="utf-8"))
hybrid_url = os.environ.get("ANSWER_BOOK_HYBRID_URL", "").strip()
hybrid_token = os.environ.get("ANSWER_BOOK_HYBRID_TOKEN", "").strip()
hybrid_config.update(
    {
        "enabled": bool(hybrid_url and hybrid_token),
        "base_url": hybrid_url,
        "tenant_id": os.environ.get("ANSWER_BOOK_HYBRID_TENANT", "default").strip() or "default",
        "client_id": "",
        "token": hybrid_token,
    }
)
hybrid_config_build = GENERATED_ROOT / "hybrid_cloud.json"
hybrid_config_build.write_text(json.dumps(hybrid_config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

datas = [
    (str(ROOT / "web"), "web"),
    (str(ROOT / "config" / "providers.example.json"), "config"),
    (str(ROOT / "config" / "model_pricing.example.json"), "config"),
    (str(ROOT / "config" / "task_defaults.json"), "config"),
    (str(ROOT / "config" / "update.json"), "config"),
    (str(support_config_build), "config"),
    (str(ROOT / "config" / "hybrid_cloud.example.json"), "config"),
    (str(hybrid_config_build), "config"),
    (str(ROOT / "APP_VERSION"), "."),
    (str(ROOT / "VERSION"), "."),
    (str(ROOT / "SOFTWARE_LICENSE.md"), "."),
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
    name="真题解析与生题平台",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ROOT / "assets" / "app-icon" / "app-icon.icns") if sys.platform == "darwin" else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="真题解析与生题平台",
)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="真题解析与生题平台.app",
        icon=str(ROOT / "assets" / "app-icon" / "app-icon.icns"),
        bundle_identifier="cn.answerbook.platform",
        info_plist={
            "CFBundleDisplayName": "真题解析与生题平台",
            "CFBundleName": "真题解析与生题平台",
            "CFBundleShortVersionString": APP_VERSION,
            "CFBundleVersion": APP_VERSION,
            "NSHighResolutionCapable": True,
            "NSLocalNetworkUsageDescription": "用于在局域网内查看本机任务状态和运行日志。",
        },
    )
