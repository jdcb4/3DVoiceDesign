param([string]$Workspace)
$ErrorActionPreference = 'Stop'
$pythonPath = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
$stopArguments = @('-m', 'voicedesign')
if ($Workspace) { $stopArguments += @('--workspace', $Workspace) }
$stopArguments += 'stop'
& $pythonPath @stopArguments
if ($LASTEXITCODE -ne 0) { throw 'Workbench shutdown failed.' }
