param(
  [string]$PluginRoot = (Split-Path -Parent $PSScriptRoot),
  [switch]$Uninstall
)

$python = (Get-Command python.exe, python3.exe, py.exe -ErrorAction SilentlyContinue | Select-Object -First 1).Source
if (-not $python) {
  throw "Python executable not found in PATH."
}

$script = Join-Path $PSScriptRoot 'install_hooks.py'
$argsList = @($script)
if ($PluginRoot) {
  $env:HEADROOM_PLUGIN_ROOT = $PluginRoot
}
if ($Uninstall) {
  $argsList += '--uninstall'
}

& $python $argsList

