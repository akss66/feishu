[CmdletBinding()]
param(
    [string]$PythonPath = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot

$env:INGESTION_DNS_MODE = "cloudflare_doh"
$env:INGESTION_SCHEDULER_ENABLED = "true"
$env:INTELLIGENCE_ANALYSIS_ENABLED = "true"
$env:INTELLIGENCE_DAILY_REPORT_ENABLED = "true"
$env:INTELLIGENCE_ALERTS_ENABLED = "true"
$env:INTELLIGENCE_QA_ENABLED = "true"
$env:DEEPSEEK_TIMEOUT_SECONDS = "60"
$env:LOG_LEVEL = "INFO"

if (-not $PythonPath) {
    $candidates = @(
        (Join-Path $projectRoot ".venv\Scripts\python.exe"),
        (Join-Path $env:USERPROFILE ".python\python.exe")
    )
    $PythonPath = $candidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    if (-not $PythonPath) {
        $pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
        if ($null -ne $pythonCommand) {
            $PythonPath = $pythonCommand.Source
        }
    }
}

if (-not $PythonPath -or -not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
    throw "A Python 3.11 executable could not be found."
}

$runtimeDirectory = Join-Path $projectRoot "data\runtime"
New-Item -ItemType Directory -Path $runtimeDirectory -Force | Out-Null
$restartDelaySeconds = 10
$ErrorActionPreference = "Continue"
while ($true) {
    $stamp = Get-Date -Format "yyyyMMdd"
    $standardLog = Join-Path $runtimeDirectory "bot-$stamp.stdout.log"
    $errorLog = Join-Path $runtimeDirectory "bot-$stamp.stderr.log"

    & $PythonPath -m commerce_agent 1>> $standardLog 2>> $errorLog
    $agentExitCode = $LASTEXITCODE
    Start-Sleep -Seconds $restartDelaySeconds
}
