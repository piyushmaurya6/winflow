# PowerShell 1-Liner & Local Installer for WinFlow
# Usage (Remote 1-liner):
#   irm https://raw.githubusercontent.com/piyushmaurya6/winflow/main/scripts/install.ps1 | iex

$ErrorActionPreference = "Stop"

Write-Host "`n  ====================================================" -ForegroundColor Cyan
Write-Host "    WinFlow Installer — Fast AI Dictation for Windows" -ForegroundColor Cyan
Write-Host "  ====================================================`n" -ForegroundColor Cyan

# Determine target directory: if run inside a cloned/extracted folder, use it; otherwise install to ~/.winflow
$LocalCheck = if ($PSScriptRoot) { Split-Path -Parent $PSScriptRoot } else { "" }
if ($LocalCheck -and (Test-Path (Join-Path $LocalCheck "pyproject.toml"))) {
    $InstallDir = $LocalCheck
    Write-Host "[1/4] Using existing WinFlow directory: $InstallDir" -ForegroundColor Gray
} else {
    $InstallDir = Join-Path $HOME "WinFlow"
    Write-Host "[1/4] Downloading latest WinFlow release to $InstallDir ..." -ForegroundColor Gray
    $ZipUrl = "https://github.com/piyushmaurya6/winflow/releases/latest/download/WinFlow-v1.0.0-Windows-x64.zip"
    $TmpZip = Join-Path $env:TEMP "winflow_release.zip"
    try {
        Invoke-WebRequest -Uri $ZipUrl -OutFile $TmpZip -UseBasicParsing
    } catch {
        $ZipUrl = "https://github.com/piyushmaurya6/winflow/archive/refs/heads/main.zip"
        Invoke-WebRequest -Uri $ZipUrl -OutFile $TmpZip -UseBasicParsing
    }
    New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
    $ExtractTmp = Join-Path $env:TEMP "winflow_extract_$([guid]::NewGuid().ToString('N'))"
    Expand-Archive -Path $TmpZip -DestinationPath $ExtractTmp -Force
    $SubFolder = Get-ChildItem -Path $ExtractTmp -Directory | Select-Object -First 1
    if ($SubFolder -and (Test-Path (Join-Path $SubFolder.FullName "pyproject.toml"))) {
        Copy-Item -Path (Join-Path $SubFolder.FullName "*") -Destination $InstallDir -Recurse -Force
    } else {
        Copy-Item -Path (Join-Path $ExtractTmp "*") -Destination $InstallDir -Recurse -Force
    }
    Remove-Item $TmpZip, $ExtractTmp -Recurse -Force -ErrorAction SilentlyContinue
}

# Ensure uv is available
$UvExe = (Get-Command uv -ErrorAction SilentlyContinue).Source
if (-not $UvExe) {
    $CandidateUv = Join-Path $HOME ".local\bin\uv.exe"
    if (Test-Path $CandidateUv) {
        $UvExe = $CandidateUv
    } else {
        Write-Host "[2/4] Installing uv Python manager..." -ForegroundColor Gray
        powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
        $UvExe = Join-Path $HOME ".local\bin\uv.exe"
    }
} else {
    Write-Host "[2/4] Found uv at $UvExe" -ForegroundColor Gray
}

Set-Location $InstallDir
Write-Host "[3/4] Setting up isolated Python 3.12 environment & dependencies..." -ForegroundColor Gray
& $UvExe venv --python 3.12 .venv
& $UvExe pip install --python .venv\Scripts\python.exe -r pyproject.toml

# Create Desktop & Start Menu shortcuts with custom WinFlow icon
Write-Host "[4/4] Creating Desktop & Start Menu shortcuts..." -ForegroundColor Gray
$WshShell = New-Object -ComObject WScript.Shell
$IconPath = Join-Path $InstallDir "assets\winflow.ico"
$VbsPath = Join-Path $InstallDir "Start-WinFlow.vbs"

foreach ($DestDir in @(
    [Environment]::GetFolderPath("Desktop"),
    (Join-Path ([Environment]::GetFolderPath("Programs")) "")
)) {
    if (Test-Path $DestDir) {
        $Shortcut = $WshShell.CreateShortcut((Join-Path $DestDir "WinFlow.lnk"))
        $Shortcut.TargetPath = "wscript.exe"
        $Shortcut.Arguments = "`"$VbsPath`""
        $Shortcut.WorkingDirectory = $InstallDir
        if (Test-Path $IconPath) {
            $Shortcut.IconLocation = "$IconPath,0"
        }
        $Shortcut.Description = "WinFlow — Fast AI Voice Dictation (Alt + X)"
        $Shortcut.Save()
    }
}

Write-Host "`n  WinFlow is installed! Launching now..." -ForegroundColor Green
Write-Host "  -> Press Alt + X anywhere to dictate (Tap to toggle, Hold for push-to-talk)." -ForegroundColor White
Write-Host "  -> Native Windows Voice Typing remains untouched on Win + H.`n" -ForegroundColor DarkGray

Start-Process -FilePath "wscript.exe" -ArgumentList "`"$VbsPath`"" -WorkingDirectory $InstallDir
