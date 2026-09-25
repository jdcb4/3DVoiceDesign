param([string]$SkillRoot)
$ErrorActionPreference = 'Stop'
if (-not $SkillRoot) {
    $codexSkillBase = if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $env:USERPROFILE '.codex' }
    $SkillRoot = Join-Path $codexSkillBase 'skills'
}
$skillSource = Join-Path $PSScriptRoot 'integrations\codex\voicedesign3d'
$skillDestination = Join-Path $SkillRoot 'voicedesign3d'
New-Item -ItemType Directory -Path (Join-Path $skillDestination 'agents') -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $skillSource 'SKILL.md') -Destination (Join-Path $skillDestination 'SKILL.md') -Force
Copy-Item -LiteralPath (Join-Path $skillSource 'agents\openai.yaml') -Destination (Join-Path $skillDestination 'agents\openai.yaml') -Force
Write-Output "Installed personal skill: $skillDestination"
