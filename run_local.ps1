$pythonCandidates = @(
    "python",
    "py",
    "$env:LocalAppData\Programs\Python\Python312\python.exe",
    "$env:LocalAppData\Programs\Python\Python311\python.exe",
    "$env:LocalAppData\Programs\Python\Python310\python.exe"
)

$pythonExe = $null

foreach ($candidate in $pythonCandidates) {
    try {
        if ($candidate -match "python(\.exe)?$" -and -not ($candidate -like "*\*")) {
            $command = Get-Command $candidate -ErrorAction SilentlyContinue
            if ($command -and $command.Source -notlike "*WindowsApps*") {
                $pythonExe = $command.Source
                break
            }
        } elseif (Test-Path $candidate) {
            $pythonExe = $candidate
            break
        }
    } catch {
    }
}

if (-not $pythonExe) {
    Write-Host "Python was not found. Install Python 3.10+ and run this script again." -ForegroundColor Red
    exit 1
}

Write-Host "Using Python: $pythonExe" -ForegroundColor Cyan
& $pythonExe -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) {
    Write-Host "Dependency installation failed." -ForegroundColor Red
    exit $LASTEXITCODE
}

& $pythonExe app.py
