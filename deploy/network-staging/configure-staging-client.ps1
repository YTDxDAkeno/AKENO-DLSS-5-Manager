# Run only in a separate Windows staging-build checkout, before PyInstaller.
# Public keys only; no production repository variables are changed.
param(
    [Parameter(Mandatory=$true)][string]$StagingKeysFile,
    [Parameter(Mandatory=$true)][string]$ExpectedCommit
)
$ErrorActionPreference = 'Stop'
$actual = (git rev-parse HEAD).Trim()
if ($actual -ne $ExpectedCommit -or $ExpectedCommit -notmatch '^[a-f0-9]{40}$') {
    throw 'Unexpected source commit; no configuration written.'
}
$doc = Get-Content -Raw -LiteralPath $StagingKeysFile | ConvertFrom-Json
if ($doc.server_url -ne 'https://lan-staging-api.akeno.online' -or @($doc.keys).Count -lt 1) {
    throw 'Wrong staging public-key document.'
}
$production = Get-Content -Raw -LiteralPath 'akeno/data/licensing.json' | ConvertFrom-Json
$pairs = @()
foreach ($key in $doc.keys) {
    if ($key.kid -notmatch '^[a-f0-9]{16}$' -or $key.public_key -notmatch '^[A-Za-z0-9_-]{43}$') {
        throw 'Invalid staging key encoding.'
    }
    if (@($production.keys | Where-Object { $_.kid -eq $key.kid }).Count -gt 0) {
        throw 'Staging key overlaps keys already trusted by the unmodified build; inspect manually.'
    }
    $pairs += "$($key.kid):$($key.public_key)"
}
python tools/configure-licensing.py --server-url 'https://lan-staging-api.akeno.online' --website-url 'https://lan-staging.akeno.online' --keys ($pairs -join ',') --require
if ($LASTEXITCODE -ne 0) { throw 'Staging licensing configuration failed.' }
Write-Host 'STAGING_LICENSE_CONFIGURATION_WRITTEN_PUBLIC_VALUES_ONLY'
Write-Host 'Use a separate Windows test user. Do not upload this build to the production updater.'
Write-Host 'Release-key files remain unchanged; prevent CI from overwriting staging licensing settings.'
