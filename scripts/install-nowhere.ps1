param(
    [Parameter(Mandatory = $true)]
    [string]$RepositoryPath
)

$ErrorActionPreference = 'Stop'
$root = [System.IO.Path]::GetFullPath($RepositoryPath).TrimEnd('\', '/')
$revision = 'f0803c1053e5f97a6f44bab26e15043d71825ec7'
$repository = 'https://github.com/yuyixuanfu/nowhere.git'
$runtime = Join-Path $root ".runtime\nowhere\$revision"
$source = Join-Path $runtime 'source'
$venv = Join-Path $runtime 'venv'
$python = Join-Path $venv 'Scripts\python.exe'
$temp = Join-Path $root '.local-tmp'

New-Item -ItemType Directory -Force -Path $temp | Out-Null
$env:TEMP = $temp
$env:TMP = $temp
$env:PYTHONUTF8 = '1'

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw 'Git is required to fetch the pinned Nowhere source.'
}
if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
    throw 'Python 3.11 or 3.12 with the Windows py launcher is required.'
}
& py -3 -c "import sys; assert (3, 11) <= sys.version_info[:2] < (3, 13)"
if ($LASTEXITCODE -ne 0) { throw 'Python 3.11 or 3.12 is required for the Nowhere runtime.' }

if (-not (Test-Path -LiteralPath $source)) {
    New-Item -ItemType Directory -Force -Path $runtime | Out-Null
    New-Item -ItemType Directory -Path $source | Out-Null
    & git -C $source init --quiet
    if ($LASTEXITCODE -ne 0) { throw 'Could not initialize the pinned Nowhere source folder.' }
    & git -C $source remote add origin $repository
    if ($LASTEXITCODE -ne 0) { throw 'Could not configure the Nowhere source remote.' }
    & git -C $source fetch --depth 1 origin $revision
    if ($LASTEXITCODE -ne 0) { throw 'Could not download the pinned Nowhere revision.' }
    & git -C $source checkout --detach FETCH_HEAD
    if ($LASTEXITCODE -ne 0) { throw 'Could not select the pinned Nowhere revision.' }
}

$actualRevision = (& git -C $source rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or $actualRevision -ne $revision) {
    throw "Nowhere source is not at the required revision $revision; it was left untouched."
}

if (-not (Test-Path -LiteralPath $python)) {
    & py -3 -m venv $venv
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the isolated Nowhere Python environment.' }
}
& $python -m pip install --disable-pip-version-check --no-cache-dir -r (Join-Path $root 'requirements.nowhere.lock.txt')
if ($LASTEXITCODE -ne 0) { throw 'Could not install the locked Nowhere dependencies.' }
& $python -c "import fastmcp, numpy, scipy, timezonefinder"
if ($LASTEXITCODE -ne 0) { throw 'The isolated Nowhere environment did not pass its import check.' }

Write-Output "Nowhere $revision is installed in its own Python environment."
