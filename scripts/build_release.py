"""
Builds native standalone WinFlow.exe (PyInstaller x64, PerMonitorV2 DPI manifest, no UPX) and
WinFlow-Setup.exe (native C# x64 installer licensed by Piyush Maurya).
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BUILD_DIR = PROJECT_ROOT / "build"
DIST_DIR = PROJECT_ROOT / "dist"
RELEASE_DIR = PROJECT_ROOT / "release_artifacts"
CSC_EXE = Path(r"C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe")

APP_MANIFEST_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<assembly xmlns="urn:schemas-microsoft-com:asm.v1" manifestVersion="1.0">
  <trustInfo xmlns="urn:schemas-microsoft-com:asm.v3">
    <security>
      <requestedPrivileges>
        <requestedExecutionLevel level="asInvoker" uiAccess="false"/>
      </requestedPrivileges>
    </security>
  </trustInfo>
  <compatibility xmlns="urn:schemas-microsoft-com:compatibility.v1">
    <application>
      <supportedOS Id="{8e0f7a12-bfb3-4fe8-b9a5-48fd50a15a9a}"/>
      <supportedOS Id="{1f676c76-80e1-4239-95bb-83d0f6d0da78}"/>
      <supportedOS Id="{4a2f28e3-53b9-4441-ba9c-d69d4a6e38}"/>
    </application>
  </compatibility>
  <application xmlns="urn:schemas-microsoft-com:asm.v3">
    <windowsSettings>
      <dpiAware xmlns="http://schemas.microsoft.com/SMI/2005/WindowsSettings">true/pm</dpiAware>
      <dpiAwareness xmlns="http://schemas.microsoft.com/SMI/2016/WindowsSettings">PerMonitorV2</dpiAwareness>
      <longPathAware xmlns="http://schemas.microsoft.com/SMI/2016/WindowsSettings">true</longPathAware>
    </windowsSettings>
  </application>
  <dependency>
    <dependentAssembly>
      <assemblyIdentity type="win32" name="Microsoft.Windows.Common-Controls" version="6.0.0.0" processorArchitecture="*" publicKeyToken="6595b64144ccf1df" language="*"/>
    </dependentAssembly>
  </dependency>
</assembly>
"""

VERSION_INFO_TEXT = """# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=(1, 3, 1, 0),
    prodvers=(1, 3, 1, 0),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo(
      [
        StringTable(
          '040904B0',
          [
            StringStruct('CompanyName', 'Piyush Maurya'),
            StringStruct('FileDescription', 'WinFlow'),
            StringStruct('FileVersion', '1.3.1.0'),
            StringStruct('InternalName', 'WinFlow'),
            StringStruct('LegalCopyright', 'Copyright (c) 2026 Piyush Maurya'),
            StringStruct('OriginalFilename', 'WinFlow.exe'),
            StringStruct('ProductName', 'WinFlow'),
            StringStruct('ProductVersion', '1.3.1')
          ]
        )
      ]
    ),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""


def main() -> None:
    if not CSC_EXE.exists():
        raise RuntimeError(f"64-bit C# compiler not found at {CSC_EXE}")

    for d in (BUILD_DIR, DIST_DIR, RELEASE_DIR):
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True, exist_ok=True)

    ver_file = BUILD_DIR / "version_info.txt"
    ver_file.write_text(VERSION_INFO_TEXT, encoding="utf-8")

    ico_path = PROJECT_ROOT / "assets" / "winflow.ico"

    print("[1/5] Building native WinFlow.exe via PyInstaller (--onedir --windowed --noupx)...")
    pyinstaller_cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onedir",
        "--windowed",
        "--noupx",
        "--name",
        "WinFlow",
        "--icon",
        str(ico_path),
        "--version-file",
        str(ver_file),
        "--distpath",
        str(DIST_DIR),
        "--workpath",
        str(BUILD_DIR / "pyi_work"),
        "--specpath",
        str(BUILD_DIR),
        "--add-data",
        f"{PROJECT_ROOT / 'assets'};assets",
        "--collect-data",
        "faster_whisper",
        "--collect-binaries",
        "ctranslate2",
        "--collect-binaries",
        "onnxruntime",
        "--collect-data",
        "sounddevice",
        "--collect-all",
        "webview",
        "--exclude-module",
        "tkinter",
        "--exclude-module",
        "unittest",
        "--exclude-module",
        "pip",
        "--exclude-module",
        "setuptools",
        str(PROJECT_ROOT / "run_winflow.py"),
    ]
    subprocess.run(pyinstaller_cmd, cwd=str(PROJECT_ROOT), check=True)

    app_dist = DIST_DIR / "WinFlow"
    assert (app_dist / "WinFlow.exe").exists(), "WinFlow.exe missing after PyInstaller build!"

    top_assets = app_dist / "assets"
    top_assets.mkdir(exist_ok=True)
    shutil.copy2(ico_path, top_assets / "winflow.ico")
    shutil.copy2(PROJECT_ROOT / "LICENSE", app_dist / "LICENSE.txt")

    for forbidden in (
        list(app_dist.rglob("config.json"))
        + list(app_dist.rglob("*.log"))
        + list(app_dist.rglob("*.bat"))
        + list(app_dist.rglob("*.vbs"))
    ):
        raise RuntimeError(f"Forbidden file inside bundle: {forbidden}")

    print("[2/5] Compiling native 64-bit Uninstall-WinFlow.exe...")
    uninstall_exe = app_dist / "Uninstall-WinFlow.exe"
    subprocess.run(
        [
            str(CSC_EXE),
            "/nologo",
            "/optimize+",
            "/target:winexe",
            "/platform:x64",
            f"/win32icon:{ico_path}",
            f"/out:{uninstall_exe}",
            "/r:System.dll",
            "/r:System.Drawing.dll",
            "/r:System.Windows.Forms.dll",
            str(PROJECT_ROOT / "installer" / "WinFlowUninstall.cs"),
        ],
        cwd=str(PROJECT_ROOT),
        check=True,
    )
    assert uninstall_exe.exists(), "Uninstall-WinFlow.exe failed to build!"

    print("[3/5] Compressing embedded installer payload...")
    payload_zip = BUILD_DIR / "payload.zip"
    all_files = [p for p in app_dist.rglob("*") if p.is_file()]
    with zipfile.ZipFile(payload_zip, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for file_path in all_files:
            arcname = file_path.relative_to(app_dist).as_posix()
            zf.write(file_path, arcname)
    print(f"      Payload size: {payload_zip.stat().st_size / (1024 * 1024):.2f} MB ({len(all_files)} files)")

    print("[4/5] Compiling standalone WinFlow-Setup.exe (Licensed by Piyush Maurya)...")
    setup_exe = RELEASE_DIR / "WinFlow-Setup.exe"
    subprocess.run(
        [
            str(CSC_EXE),
            "/nologo",
            "/optimize+",
            "/target:winexe",
            "/platform:x64",
            f"/win32icon:{ico_path}",
            f"/out:{setup_exe}",
            "/r:System.dll",
            "/r:System.Drawing.dll",
            "/r:System.Windows.Forms.dll",
            "/r:System.IO.Compression.dll",
            "/r:System.IO.Compression.FileSystem.dll",
            f"/resource:{payload_zip},WinFlowPayload.zip",
            str(PROJECT_ROOT / "installer" / "WinFlowSetup.cs"),
        ],
        cwd=str(PROJECT_ROOT),
        check=True,
    )
    assert setup_exe.exists(), "WinFlow-Setup.exe failed to build!"
    print(f"      WinFlow-Setup.exe size: {setup_exe.stat().st_size / (1024 * 1024):.2f} MB")

    print("[5/5] Creating WinFlow-Portable-x64.zip...")
    portable_zip = RELEASE_DIR / "WinFlow-Portable-x64.zip"
    with zipfile.ZipFile(portable_zip, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for file_path in all_files:
            arcname = f"WinFlow/{file_path.relative_to(app_dist).as_posix()}"
            zf.write(file_path, arcname)
    print(f"      WinFlow-Portable-x64.zip size: {portable_zip.stat().st_size / (1024 * 1024):.2f} MB")
    print("BUILD COMPLETE!")


if __name__ == "__main__":
    main()
