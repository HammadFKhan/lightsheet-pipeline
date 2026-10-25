# Creates a private Python environment (.venv) next to this file and installs the pipeline.
#
#   powershell -ExecutionPolicy Bypass -File setup_env.ps1                 # CPU only
#   powershell -ExecutionPolicy Bypass -File setup_env.ps1 -Gpu cuda13     # + NVIDIA GPU support (CUDA 13 driver)
#   powershell -ExecutionPolicy Bypass -File setup_env.ps1 -Gpu cuda12     # + NVIDIA GPU support (CUDA 12 driver)
#
# Needs Python 3.10-3.12 (python.org installer, with the "py" launcher). If pip fails with "CERTIFICATE_VERIFY_FAILED"
# (institutional networks that inspect HTTPS), this script automatically installs `truststore` so pip trusts the Windows
# certificate store, and retries.
param(
    [ValidateSet("none", "cuda13", "cuda12")][string]$Gpu = "none",
    [string]$Requirements = "requirements.txt",
    [switch]$SkipTests
)
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot

$py = $null
foreach ($v in "3.10", "3.11", "3.12") {
    & py "-$v" -c "import sys" 2>$null
    if ($LASTEXITCODE -eq 0) { $py = "-$v"; break }
}
if (-not $py) { throw "Python 3.10, 3.11 or 3.12 was not found. Install it from python.org (tick 'py launcher') and run this again." }
Write-Host "Using Python $py"

$venv = Join-Path $root ".venv"
$vp = Join-Path $venv "Scripts\python.exe"
if (-not (Test-Path $vp)) { & py $py -m venv $venv; if ($LASTEXITCODE -ne 0) { throw "Could not create the virtual environment." } }

function Install-Packages([string[]]$pipArgs) {
    & $vp -m pip install @pipArgs | Out-Host        # Out-Host: show pip's output without mixing it into the return value
    return [int]$LASTEXITCODE
}

$req = Join-Path $root $Requirements
Write-Host "Installing packages (this downloads about 2 GB the first time)..."
if ((Install-Packages @("-r", $req)) -ne 0) {
    Write-Host "pip failed - most likely an HTTPS certificate problem. Installing 'truststore' and retrying..."
    $meta = Invoke-RestMethod "https://pypi.org/pypi/truststore/json"          # PowerShell uses the Windows certificate store
    $whl = $meta.urls | Where-Object { $_.filename -like "*.whl" } | Select-Object -First 1
    $tmp = Join-Path $env:TEMP $whl.filename
    Invoke-WebRequest $whl.url -OutFile $tmp
    & $vp -m pip install $tmp
    if ((Install-Packages @("--use-feature=truststore", "-r", $req)) -ne 0) { throw "Installation failed. See the messages above." }
    $trust = @("--use-feature=truststore")
} else { $trust = @() }

if ($Gpu -ne "none") {
    $pkg = if ($Gpu -eq "cuda13") { "cupy-cuda13x[ctk]" } else { "cupy-cuda12x[ctk]" }
    Write-Host "Installing GPU support ($pkg)..."
    if ((Install-Packages ($trust + @($pkg))) -ne 0) { Write-Warning "GPU package failed to install - the pipeline still works on CPU." }
}

if (-not $SkipTests) {
    Write-Host "`nChecking the install..."
    & $vp (Join-Path $root "tests\test_core.py")
}
Write-Host "`nDone. Start the app with run_gui.bat"
