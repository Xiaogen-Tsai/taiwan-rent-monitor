param(
    [string]$TaskName = "TaiwanRentWatch",
    [datetime]$StartAt = (Get-Date).AddMinutes(1)
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
$PythonExe = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$HiddenLauncher = Join-Path $ProjectRoot "scripts\run_watch_hidden.vbs"

if (-not (Test-Path -LiteralPath $PythonExe)) {
    throw "Project Python was not found: $PythonExe. Complete the README setup first."
}
if (-not (Test-Path -LiteralPath $HiddenLauncher)) {
    throw "Hidden launcher was not found: $HiddenLauncher"
}

Push-Location $ProjectRoot
try {
    $intervalText = & $PythonExe -B -c "from rent_bot.config import get_settings; print(get_settings().crawl_interval_minutes)"
    if ($LASTEXITCODE -ne 0) {
        throw "Could not read schedule.interval_minutes from rent_config.toml."
    }
}
finally {
    Pop-Location
}

$IntervalMinutes = 0
if (-not [int]::TryParse(($intervalText | Select-Object -Last 1), [ref]$IntervalMinutes)) {
    throw "schedule.interval_minutes is not a valid integer: $intervalText"
}
if ($IntervalMinutes -lt 15 -or $IntervalMinutes -gt 1440) {
    throw "schedule.interval_minutes must be between 15 and 1440 minutes."
}

$ExecutionLimitMinutes = [Math]::Max(12, [Math]::Min(30, $IntervalMinutes - 3))
$Action = New-ScheduledTaskAction `
    -Execute "wscript.exe" `
    -Argument ('"{0}"' -f $HiddenLauncher) `
    -WorkingDirectory $ProjectRoot
$Trigger = New-ScheduledTaskTrigger `
    -Once `
    -At $StartAt `
    -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes)
$Settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Minutes $ExecutionLimitMinutes) `
    -MultipleInstances IgnoreNew
$CurrentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$Principal = New-ScheduledTaskPrincipal `
    -UserId $CurrentUser `
    -LogonType Interactive `
    -RunLevel Limited

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -Principal $Principal `
    -Description "Taiwan rental monitor. Interval comes from rent_config.toml; missed runs start when Windows is next available." `
    -Force | Out-Null

$Task = Get-ScheduledTask -TaskName $TaskName
$TaskInfo = Get-ScheduledTaskInfo -TaskName $TaskName
[pscustomobject]@{
    TaskName = $Task.TaskName
    State = $Task.State
    IntervalMinutes = $IntervalMinutes
    StartWhenAvailable = $Task.Settings.StartWhenAvailable
    NextRunTime = $TaskInfo.NextRunTime
    Action = ($Task.Actions | ForEach-Object { ($_.Execute + " " + $_.Arguments).Trim() }) -join "; "
}
