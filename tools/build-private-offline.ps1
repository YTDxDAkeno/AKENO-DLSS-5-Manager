[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$NeuralRuntime,
    [string]$BackendArchive,
    [string]$RuntimeSource = '',
    [string]$SuperResolutionRuntime,
    [string]$OutputDirectory = '.\dist-private',
    [switch]$LocalUsePermitted,
    [string]$Python = 'python',
    [string]$InnoCompiler = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
)
$ErrorActionPreference = 'Stop'
if ($env:GITHUB_ACTIONS -eq 'true') { throw 'Private bundles must not be built in public GitHub Actions.' }
if (-not $LocalUsePermitted) { throw 'Supply -LocalUsePermitted only if you have rights for personal local reuse.' }
$repo = Split-Path $PSScriptRoot -Parent
Push-Location $repo
try {
    $out = [IO.Path]::GetFullPath($OutputDirectory)
    if (Test-Path $out) { throw 'Choose a new output directory; existing packages are never overwritten.' }
    if (-not (Test-Path $InnoCompiler)) { throw 'Install Inno Setup 6 first or supply -InnoCompiler.' }
    & $Python packaging/make_icon.py
    if ($LASTEXITCODE -ne 0) { throw 'Icon build failed.' }
    & $Python -m PyInstaller --noconfirm --distpath "$out/app" --workpath "$out/work" packaging/akeno.spec
    if ($LASTEXITCODE -ne 0) { throw 'Windows application build failed.' }
    $bundleArgs = @('tools/build_private_bundle.py', '--runtime', $NeuralRuntime, '--output', "$out/app/AKENO/runtimes", '--local-use-permitted')
    if ($RuntimeSource) { $bundleArgs += @('--source', $RuntimeSource) }
    if ($SuperResolutionRuntime) { $bundleArgs += @('--sr-runtime', $SuperResolutionRuntime) }
    if ($BackendArchive) { $bundleArgs += @('--backend-archive', $BackendArchive) }
    & $Python @bundleArgs
    if ($LASTEXITCODE -ne 0) { throw 'Private runtime validation failed; no distributable archive was created.' }
    $v = (& $Python -c 'from akeno import __version__; print(__version__)').Trim()
    Copy-Item README.md, LICENSE, THIRD_PARTY_NOTICES.md "$out/app/AKENO/"
    & $InnoCompiler "/DAppVersion=$v" "/DSourceDir=$out/app/AKENO" "/DArtifactDir=$out" packaging/installer.iss
    if ($LASTEXITCODE -ne 0) { throw 'Private installer failed.' }
    Compress-Archive -Path "$out/app/AKENO/*" -DestinationPath "$out/AKENO-$v-private-portable.zip"
    Get-ChildItem $out -File | Where-Object { $_.Extension -in '.zip','.exe' } | Get-FileHash -Algorithm SHA256 | ForEach-Object {
        "$($_.Hash.ToLower())  $(Split-Path $_.Path -Leaf)"
    } | Set-Content "$out/SHA256SUMS.txt" -Encoding ascii
    Write-Host "Private package created in $out. Do not publish proprietary runtimes."
} finally { Pop-Location }
