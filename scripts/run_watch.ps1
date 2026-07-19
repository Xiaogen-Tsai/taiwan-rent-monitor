$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
$PythonExe = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$MainPy = Join-Path $ProjectRoot "main.py"
$LogDir = Join-Path $ProjectRoot "logs"
$LogFile = Join-Path $LogDir "rent-watch.log"

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONIOENCODING = "utf-8"
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)

function Write-LogLine {
    param([Parameter(Mandatory = $true)][string]$Line)

    Write-Output $Line
    [System.IO.File]::AppendAllText($LogFile, $Line + [Environment]::NewLine, $Utf8NoBom)
}

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
Set-Location $ProjectRoot

$startedAt = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
Write-LogLine "[$startedAt] Starting rent watch"

$PreviousErrorActionPreference = $ErrorActionPreference
$ErrorActionPreference = "Continue"
try {
    & $PythonExe $MainPy watch 2>&1 |
        ForEach-Object { $_.ToString() } |
        ForEach-Object { Write-LogLine $_ }
    $exitCode = if ($LASTEXITCODE -ne $null) { $LASTEXITCODE } else { 0 }
}
finally {
    $ErrorActionPreference = $PreviousErrorActionPreference
}

$finishedAt = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
Write-LogLine "[$finishedAt] Finished rent watch with exit code $exitCode"
exit $exitCode
