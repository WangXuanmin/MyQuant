param(
    [ValidateRange(1024,65535)][int]$Port = 8765,
    [switch]$NoBrowser
)
$ErrorActionPreference = 'Stop'
$projectPath = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$localPythonPath = Join-Path $projectPath '.venv\Scripts\python.exe'
$parentPythonPath = Join-Path (Split-Path $projectPath -Parent) '.venv\Scripts\python.exe'
$pythonPath = if (Test-Path -LiteralPath $localPythonPath) { $localPythonPath } else { $parentPythonPath }
$venvConfigPath = Join-Path (Split-Path (Split-Path $pythonPath -Parent) -Parent) 'pyvenv.cfg'
if (-not (Test-Path -LiteralPath $pythonPath)) { throw "Python not found: $pythonPath" }
$url = "http://127.0.0.1:$Port"
$existing = $null
try { $existing = Invoke-RestMethod -Uri "$url/api/state" -TimeoutSec 2 } catch { }
if ($existing) {
    if ($existing.app -ne 'xquant-local-console' -or $existing.project_root -ne $projectPath) {
        throw "Port $Port belongs to a different service. Choose another -Port."
    }
    Write-Host "Already running: $url"
} else {
    $logPath = Join-Path $projectPath 'data\web'
    New-Item -ItemType Directory -Path $logPath -Force | Out-Null
    $outPath = Join-Path $logPath "server-$Port.out.log"
    $errPath = Join-Path $logPath "server-$Port.err.log"
    $originalPythonPath = $env:PYTHONPATH
    try {
        $env:PYTHONPATH = Join-Path $projectPath 'src'
        $arguments = '-m xquant_assistant.cli --project-root "{0}" web --port {1}' -f $projectPath,$Port
        $process = Start-Process -FilePath $pythonPath -ArgumentList $arguments -WorkingDirectory $projectPath -WindowStyle Hidden -RedirectStandardOutput $outPath -RedirectStandardError $errPath -PassThru
    } finally { $env:PYTHONPATH = $originalPythonPath }
    $ready = $false
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        Start-Sleep -Milliseconds 300
        try {
            $state = Invoke-RestMethod -Uri "$url/api/state" -TimeoutSec 2
            if ($state.app -eq 'xquant-local-console' -and $state.project_root -eq $projectPath) { $ready = $true; break }
        } catch { }
        if ($process.HasExited) { break }
    }
    if (-not $ready) { throw "Startup failed. See $errPath" }
    @{pid=$state.process_id;launcher_pid=$process.Id;project_root=$projectPath;port=$Port;python_path=$pythonPath;venv_config=$venvConfigPath} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $logPath "server-$Port.json") -Encoding UTF8
    Write-Host "Started local simulated account console: $url"
}
if (-not $NoBrowser) { Start-Process $url }
