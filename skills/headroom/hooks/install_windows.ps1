param(
  [string]$PluginRoot = (Split-Path -Parent $PSScriptRoot),
  [switch]$Force
)

$codexHome = if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $env:USERPROFILE '.codex' }
$target = Join-Path $codexHome 'hooks.json'
if ((Test-Path -LiteralPath $target) -and -not $Force) {
  throw "Refusing to overwrite existing $target. Re-run with -Force after merging the hook entries."
}
$pythonw = (Get-Command pythonw.exe -ErrorAction Stop).Source
$template = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'hooks.json.template') -Raw
$json = $template.Replace('__PLUGIN_ROOT__', ($PluginRoot -replace '\\', '/')).Replace('__PYTHONW__', ($pythonw -replace '\\', '/'))
$null = $json | ConvertFrom-Json
New-Item -ItemType Directory -Force -Path $codexHome | Out-Null
$tmp = "$target.tmp-$PID"
Set-Content -LiteralPath $tmp -Value $json -Encoding UTF8
Move-Item -LiteralPath $tmp -Destination $target -Force
Write-Output "Installed headroom hooks at $target"
