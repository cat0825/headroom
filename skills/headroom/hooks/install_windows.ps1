param(
  [string]$PluginRoot = (Split-Path -Parent $PSScriptRoot),
  [switch]$Force
)

$codexHome = if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $env:USERPROFILE '.codex' }
$target = Join-Path $codexHome 'hooks.json'
if ((Test-Path -LiteralPath $target) -and -not $Force) {
  throw "Refusing to overwrite existing $target. Re-run with -Force after merging the hook entries."
}
$python = (Get-Command python.exe -ErrorAction Stop).Source
$template = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'hooks.json.template') -Raw
$json = $template.Replace('__PLUGIN_ROOT__', ($PluginRoot -replace '\\', '/')).Replace('__PYTHON__', ($python -replace '\\', '/'))
$null = $json | ConvertFrom-Json
New-Item -ItemType Directory -Force -Path $codexHome | Out-Null
$tmp = "$target.tmp-$PID"
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[IO.File]::WriteAllText($tmp, $json, $utf8NoBom)
Move-Item -LiteralPath $tmp -Destination $target -Force
Write-Output "Installed headroom hooks at $target"
