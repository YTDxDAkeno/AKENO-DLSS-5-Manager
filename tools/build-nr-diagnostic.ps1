# Explicit local build only; never installs into a game or alters NVIDIA runtime files.
param([Parameter(Mandatory=$true)][string]$Source)
$ErrorActionPreference = 'Stop'
python "$PSScriptRoot/prepare_nr_diagnostic.py" $Source
if ($LASTEXITCODE -ne 0) { throw 'Pinned diagnostic source preparation failed' }
msbuild "$Source/OptiScaler.sln" /m /p:Configuration=Release /p:Platform=x64 /verbosity:minimal
if ($LASTEXITCODE -ne 0) { throw 'Diagnostic compilation failed' }
python "$PSScriptRoot/package_nr_diagnostic.py" $Source
if ($LASTEXITCODE -ne 0) { throw 'Local diagnostic inventory failed' }
