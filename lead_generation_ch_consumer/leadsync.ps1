# Thabo opt-in lead sync as a hidden background process (no admin rights, no Claude session needed).
#   .\lead_generation_ch_consumer\leadsync.ps1 START [-AtLogon]   # hourly sync; -AtLogon also starts it after each login
#   .\lead_generation_ch_consumer\leadsync.ps1 STOP                 # ends the process AND removes the login autostart
#   .\lead_generation_ch_consumer\leadsync.ps1 STATUS
#   .\lead_generation_ch_consumer\leadsync.ps1 REPORT
# shortcut: no Task Scheduler (denied without elevation here), so a crashed interpreter is not restarted
# until the next login/START; upgrade to a scheduled task if it ever runs on an elevated account.
param(
    [Parameter(Mandatory)][ValidateSet('START', 'STOP', 'STATUS', 'REPORT')][string]$Command,
    [switch]$AtLogon
)
$ErrorActionPreference = 'Stop'
$Repo = Split-Path $PSScriptRoot -Parent
$Folder = Join-Path $Repo 'Lead-Sammelordner'
$Args_ = '-m lead_generation_ch_consumer.sync_service run'
$Link = Join-Path ([Environment]::GetFolderPath('Startup')) 'ThaboLeadSync.lnk'
# First real interpreter on PATH; Store aliases (WindowsApps) cannot be started detached.
$Python = (Get-Command python -All | Where-Object Source -notlike '*WindowsApps*' | Select-Object -First 1).Source
if (-not $Python) { throw 'python.exe nicht gefunden' }
$PythonW = Join-Path (Split-Path $Python) 'pythonw.exe'  # no console window

function Get-Sync { @(Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" | Where-Object CommandLine -like "*sync_service run*") }

switch ($Command) {
    'START' {
        New-Item -ItemType Directory -Force $Folder | Out-Null
        icacls $Folder /inheritance:r /grant:r "$($env:USERNAME):(OI)(CI)F" | Out-Null  # owner-only access
        if ($AtLogon) {
            $s = (New-Object -ComObject WScript.Shell).CreateShortcut($Link)
            $s.TargetPath = $PythonW; $s.Arguments = $Args_; $s.WorkingDirectory = $Repo; $s.Save()
        }
        if (Get-Sync) { "LAEUFT BEREITS" + $(if ($AtLogon) { ' (Autostart nach Login eingerichtet)' } else { '' }); break }
        Start-Process -FilePath $PythonW -ArgumentList $Args_ -WorkingDirectory $Repo -WindowStyle Hidden
        "GESTARTET: erster Zyklus sofort, danach alle 60 Minuten" + $(if ($AtLogon) { ', Autostart nach Login eingerichtet' } else { '' })
    }
    'STOP' {
        Remove-Item $Link -ErrorAction SilentlyContinue  # first, so nothing can bring it back
        $p = Get-Sync
        $p | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
        "GESTOPPT: $($p.Count) Prozess(e) beendet, Autostart entfernt. Lead-Sammelordner bleibt erhalten."
    }
    'STATUS' {
        $p = Get-Sync
        $auto = if (Test-Path $Link) { 'Autostart nach Login: an' } else { 'Autostart nach Login: aus' }
        if ($p) { "STATUS: laeuft (PID $($p.ProcessId -join ', '), seit $($p[0].CreationDate)) | $auto" }
        else { "STATUS: gestoppt | $auto" }
    }
    'REPORT' {
        Push-Location $Repo
        try { & $Python -m lead_generation_ch_consumer.sync_service report } finally { Pop-Location }
    }
}
