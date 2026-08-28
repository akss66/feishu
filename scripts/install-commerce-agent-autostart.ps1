[CmdletBinding()]
param(
    [string]$TaskName = "CrossBorderCommerceAgent",
    [switch]$StartNow
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$runnerPath = Join-Path $PSScriptRoot "run-commerce-agent.ps1"
if (-not (Test-Path -LiteralPath $runnerPath -PathType Leaf)) {
    throw "The managed runner script is missing."
}

$currentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$powerShellPath = Join-Path $PSHOME "powershell.exe"
$arguments = '-NoProfile -NonInteractive -ExecutionPolicy RemoteSigned -File "{0}"' -f $runnerPath
$action = New-ScheduledTaskAction -Execute $powerShellPath -Argument $arguments -WorkingDirectory (Split-Path -Parent $PSScriptRoot)
$logonTrigger = New-ScheduledTaskTrigger -AtLogOn -User $currentUser
$dailyTrigger = New-ScheduledTaskTrigger -Daily -At "08:30"
$triggers = @($logonTrigger, $dailyTrigger)
$principal = New-ScheduledTaskPrincipal -UserId $currentUser -LogonType "Interactive" -RunLevel "Limited"
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances "IgnoreNew" `
    -RestartCount 10 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -WakeToRun `
    -ExecutionTimeLimit ([TimeSpan]::Zero)

$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($null -ne $existing) {
    $managed = @($existing.Actions) | Where-Object {
        $_.Execute -eq $powerShellPath -and $_.Arguments -like "*$runnerPath*"
    }
    if (-not $managed) {
        throw "The existing task is not managed by this project. Refusing to replace it."
    }
    Set-ScheduledTask `
        -TaskName $TaskName `
        -Action $action `
        -Trigger $triggers `
        -Principal $principal `
        -Settings $settings | Out-Null
}
else {
    Register-ScheduledTask `
        -TaskName $TaskName `
        -Description "Runs the local cross-border commerce Feishu intelligence agent." `
        -Action $action `
        -Trigger $triggers `
        -Principal $principal `
        -Settings $settings | Out-Null
}

if ($StartNow) {
    Start-ScheduledTask -TaskName $TaskName
}

Get-ScheduledTask -TaskName $TaskName
