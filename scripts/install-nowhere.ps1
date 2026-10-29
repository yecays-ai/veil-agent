param(
    [Parameter(Mandatory = $true)]
    [string]$RepositoryPath,
    [string]$BootstrapPythonPath
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

if (-not (Test-Path -LiteralPath $source)) {
    New-Item -ItemType Directory -Force -Path $runtime | Out-Null
    try {
        New-Item -ItemType Directory -Path $source | Out-Null
        & git -C $source init --quiet
        if ($LASTEXITCODE -ne 0) { throw 'Could not initialize the pinned Nowhere source folder.' }
        & git -C $source remote add origin $repository
        if ($LASTEXITCODE -ne 0) { throw 'Could not configure the Nowhere source remote.' }
        & git -C $source fetch --depth 1 origin $revision
        if ($LASTEXITCODE -ne 0) { throw 'Could not download the pinned Nowhere revision.' }
        & git -C $source checkout --detach FETCH_HEAD
        if ($LASTEXITCODE -ne 0) { throw 'Could not select the pinned Nowhere revision.' }
    } catch {
        Remove-Item -LiteralPath $source -Recurse -Force -ErrorAction SilentlyContinue
        throw
    }
} else {
    $existingRevision = (& git -C $source rev-parse HEAD 2>$null)
    if ($LASTEXITCODE -ne 0) {
        if (-not (Test-Path -LiteralPath (Join-Path $source '.git'))) {
            throw 'The Nowhere source path exists without installer metadata; it was left untouched.'
        }
        Remove-Item -LiteralPath $source -Recurse -Force
        New-Item -ItemType Directory -Path $source | Out-Null
        try {
            & git -C $source init --quiet
            if ($LASTEXITCODE -ne 0) { throw 'Could not initialize the pinned Nowhere source folder.' }
            & git -C $source remote add origin $repository
            if ($LASTEXITCODE -ne 0) { throw 'Could not configure the Nowhere source remote.' }
            & git -C $source fetch --depth 1 origin $revision
            if ($LASTEXITCODE -ne 0) { throw 'Could not download the pinned Nowhere revision.' }
            & git -C $source checkout --detach FETCH_HEAD
            if ($LASTEXITCODE -ne 0) { throw 'Could not select the pinned Nowhere revision.' }
        } catch {
            Remove-Item -LiteralPath $source -Recurse -Force -ErrorAction SilentlyContinue
            throw
        }
    }
}

$actualRevision = (& git -C $source rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or $actualRevision -ne $revision) {
    throw "Nowhere source is not at the required revision $revision; it was left untouched."
}

# The pinned upstream revision has a trailing comma in the web-observer
# announcement. That turns the f-string into a one-item tuple, so `prose +=`
# raises TypeError on open_door. Build the Chinese text from code points because
# Windows PowerShell 5.1 reads BOM-less UTF-8 scripts using the system code page.
$serverFile = Join-Path $source 'nowhere\server.py'
$serverText = [System.IO.File]::ReadAllText($serverFile)
$observerText = -join [char[]]@(0xFF08, 0x65C1, 0x89C2, 0x8005, 0x53EF, 0x4EE5, 0x5728, 0x8FD9, 0x91CC, 0x770B, 0x4F60, 0x8D70, 0x8DEF, 0xFF1A, 0xFF09)
$brokenAnnouncement = '        prose += f"\n' + $observerText + '",'
$fixedAnnouncement = '        prose += f"\n' + $observerText + '"'
$brokenCount = ([regex]::Matches($serverText, [regex]::Escape($brokenAnnouncement))).Count
if ($brokenCount -eq 1) {
    $utf8NoBom = [System.Text.UTF8Encoding]::new($false)
    [System.IO.File]::WriteAllText($serverFile, $serverText.Replace($brokenAnnouncement, $fixedAnnouncement), $utf8NoBom)
} elseif ($brokenCount -gt 1) {
    throw 'The pinned Nowhere source has multiple observer announcements; refusing an ambiguous patch.'
} elseif (-not $serverText.Contains($fixedAnnouncement)) {
    throw 'The pinned Nowhere source has an unexpected observer announcement; refusing an unverified patch.'
}

if (Test-Path -LiteralPath $python) {
    & $python -c "import sys; assert sys.version_info[:2] == (3, 12)"
    if ($LASTEXITCODE -ne 0) { throw 'The existing Nowhere environment is not Python 3.12; it was left untouched.' }
} else {
    $bootstrap = $null
    if ($BootstrapPythonPath -and (Test-Path -LiteralPath $BootstrapPythonPath)) {
        $candidate = (Resolve-Path -LiteralPath $BootstrapPythonPath).Path
        & $candidate -c "import sys; assert sys.version_info[:2] == (3, 12)" 2>$null
        if ($LASTEXITCODE -eq 0) { $bootstrap = $candidate }
    }
    if (-not $bootstrap) {
        $repositoryPython = Join-Path $root '.venv\Scripts\python.exe'
        if (Test-Path -LiteralPath $repositoryPython) {
            & $repositoryPython -c "import sys; assert sys.version_info[:2] == (3, 12)" 2>$null
            if ($LASTEXITCODE -eq 0) { $bootstrap = (Resolve-Path -LiteralPath $repositoryPython).Path }
        }
    }
    if (-not $bootstrap -and (Get-Command py -ErrorAction SilentlyContinue)) {
        & py -3.12 -c "import sys; assert sys.version_info[:2] == (3, 12)" 2>$null
        if ($LASTEXITCODE -eq 0) { $bootstrap = 'py' }
    }
    if (-not $bootstrap) { throw 'Nowhere requires Python 3.12. Install it or rerun setup with a Python 3.12 VEIL environment.' }
    if ($bootstrap -eq 'py') { & py -3.12 -m venv $venv }
    else { & $bootstrap -m venv $venv }
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the isolated Nowhere Python environment.' }
}
& $python -m pip install --disable-pip-version-check --no-cache-dir --require-hashes -r (Join-Path $root 'requirements.nowhere.lock.txt')
if ($LASTEXITCODE -ne 0) { throw 'Could not install the locked Nowhere dependencies.' }
& $python -c "import fastmcp, numpy, scipy, timezonefinder"
if ($LASTEXITCODE -ne 0) { throw 'The isolated Nowhere environment did not pass its import check.' }

Write-Output "Nowhere $revision is installed in its own Python environment."
