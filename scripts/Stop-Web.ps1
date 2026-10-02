param([ValidateRange(1024,65535)][int]$Port = 8765)
$ErrorActionPreference = 'Stop'
$projectPath = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$recordPath = Join-Path $projectPath "data\web\server-$Port.json"
if (-not (Test-Path -LiteralPath $recordPath)) { Write-Host 'No launcher process record. Close the terminal that started the server.'; return }
$record = Get-Content -LiteralPath $recordPath -Raw | ConvertFrom-Json
if ($record.project_root -ne $projectPath -or $record.port -ne $Port) { throw 'Process record does not match this project.' }
$savedProcessId = [int]$record.pid
$process = Get-CimInstance Win32_Process -Filter "ProcessId = $savedProcessId"
$localPythonPath = Join-Path $projectPath '.venv\Scripts\python.exe'
$parentPythonPath = Join-Path (Split-Path $projectPath -Parent) '.venv\Scripts\python.exe'
$pythonPath = if ($record.python_path) { $record.python_path } elseif (Test-Path -LiteralPath $localPythonPath) { $localPythonPath } else { $parentPythonPath }
if ($pythonPath -notin @($localPythonPath,$parentPythonPath)) { throw 'Unexpected Python executable in process record.' }
$venvConfig = Join-Path (Split-Path (Split-Path $pythonPath -Parent) -Parent) 'pyvenv.cfg'
$homeLine = Get-Content -LiteralPath $venvConfig | Where-Object { $_ -match '^home\s*=' } | Select-Object -First 1
$basePythonPath = if ($homeLine) { Join-Path (($homeLine -split '=',2)[1].Trim()) 'python.exe' } else { $pythonPath }
if ($process) {
    if ($process.ExecutablePath -notin @($pythonPath,$basePythonPath) -or -not $process.CommandLine.Contains($projectPath) -or $process.CommandLine -notmatch 'xquant_assistant\.cli' -or $process.CommandLine -notmatch "web --port $Port\b") {
        throw 'PID now belongs to a different process; it will not be stopped.'
    }
    Stop-Process -Id $savedProcessId
    Write-Host 'Local console stopped. Stored account and reports are retained.'
} else { Write-Host 'Local console is already stopped.' }
