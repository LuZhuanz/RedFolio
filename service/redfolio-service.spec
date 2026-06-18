# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
import importlib.util
import platform
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

project_root = Path(SPECPATH).parent
service_dir = project_root / "service"

hiddenimports = []
for package in ("akshare", "fastapi", "pandas", "pydantic", "uvicorn"):
    hiddenimports += collect_submodules(package)

hiddenimports += [
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
]

datas = []
for package in ("akshare",):
    datas += collect_data_files(package)

binaries = []


def package_dir(package):
    spec = importlib.util.find_spec(package)
    if spec is None or not spec.submodule_search_locations:
        raise RuntimeError(f"Package not found: {package}")
    return Path(next(iter(spec.submodule_search_locations)))


def mini_racer_library_name():
    machine = platform.machine()
    if sys.platform == "win32":
        return "mini_racer.dll"
    if sys.platform == "darwin" and machine == "arm64":
        return "armlibmini_racer.dylib"
    if sys.platform == "darwin":
        return "libmini_racer.dylib"
    if machine == "aarch64":
        return "armlibmini_racer.glibc.so"
    return "libmini_racer.glibc.so"


py_mini_racer_dir = package_dir("py_mini_racer")
mini_racer_library = py_mini_racer_dir / mini_racer_library_name()
uses_modern_mini_racer_loader = (py_mini_racer_dir / "_dll.py").exists()
uses_legacy_mini_racer_loader = (py_mini_racer_dir / "py_mini_racer.py").exists()
if uses_modern_mini_racer_loader and uses_legacy_mini_racer_loader:
    raise RuntimeError("Conflicting py_mini_racer package files; reinstall mini-racer in the build environment.")

mini_racer_destination = "py_mini_racer" if uses_modern_mini_racer_loader else "."

if mini_racer_library.exists():
    binaries.append((str(mini_racer_library), mini_racer_destination))

mini_racer_icu_data = py_mini_racer_dir / "icudtl.dat"
if mini_racer_icu_data.exists():
    datas.append((str(mini_racer_icu_data), mini_racer_destination))

block_cipher = None


a = Analysis(
    [str(service_dir / "packaged_entry.py")],
    pathex=[str(service_dir)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["matplotlib.tests", "numpy.tests", "pandas.tests", "pytest"],
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
    name="redfolio-service",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
