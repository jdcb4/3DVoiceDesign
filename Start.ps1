param([int]$Port = 0, [string]$Workspace, [switch]$NoBrowser, [switch]$Setup)
$ErrorActionPreference = 'Stop'
$pythonPath = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
$viewerPath = Join-Path $PSScriptRoot 'voicedesign\static\index.html'
if ($Setup -or -not (Test-Path -LiteralPath $pythonPath) -or -not (Test-Path -LiteralPath $viewerPath)) {
    Push-Location -LiteralPath $PSScriptRoot
    try {
        & uv sync --frozen
        if ($LASTEXITCODE -ne 0) { throw 'Python setup failed.' }
        & npm ci --no-audit --no-fund
        if ($LASTEXITCODE -ne 0) { throw 'Viewer dependency setup failed.' }
        & npm run build
        if ($LASTEXITCODE -ne 0) { throw 'Viewer build failed.' }
    } finally { Pop-Location }
}
$launchArguments = @('-m', 'voicedesign')
if ($Workspace) { $launchArguments += @('--workspace', $Workspace) }
if ($Port) { $launchArguments += @('--port', $Port) }
$launchArguments += 'start'
if (-not $NoBrowser) { $launchArguments += '--open' }
& $pythonPath @launchArguments
if ($LASTEXITCODE -ne 0) { throw 'Workbench startup failed.' }
