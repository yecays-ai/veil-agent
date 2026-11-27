param(
    [Parameter(Mandatory = $true)]
    [string]$TargetRef,
    [string]$RepositoryPath = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path,
    [switch]$SkipPackage,
    [switch]$SkipPythonDeps,
    [switch]$AllowOnlineDeps
)

$ErrorActionPreference = 'Stop'
$root = [System.IO.Path]::GetFullPath($RepositoryPath)

function Test-SessionLockHeld {
    param([string]$RepoRoot)
    $py = Join-Path $RepoRoot '.venv\Scripts\python.exe'
    $lock = Join-Path $RepoRoot 'data\session.lock'
    if (-not (Test-Path -LiteralPath $py) -or -not (Test-Path -LiteralPath $lock)) {
        return $false
    }
    $code = "import msvcrt,sys; h=open(sys.argv[1],'a+b'); h.seek(0); msvcrt.locking(h.fileno(), msvcrt.LK_NBLCK, 1)"
    & $py -c $code $lock 2>$null
    return ($LASTEXITCODE -ne 0)
}

function Test-PythonDependencies {
    param([string]$PythonPath, [string]$LockFile)
    if (-not (Test-Path -LiteralPath $PythonPath)) {
        return @{ Ok = $false; Detail = 'Python environment is missing' }
    }
    $check = @'
import importlib.metadata, re, sys
missing = []
for raw in open(sys.argv[1], encoding='utf-8-sig'):
    line = raw.strip()
    if not line or line.startswith('#'):
        continue
    match = re.match(r'^([A-Za-z0-9_.-]+)==([^\s;]+)', line)
    if not match:
        continue
    name, wanted = match.group(1), match.group(2)
    try:
        have = importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        missing.append(f'{name}=={wanted} (missing)')
        continue
    if have != wanted:
        missing.append(f'{name}=={wanted} (have {have})')
print('\n'.join(missing))
raise SystemExit(1 if missing else 0)
'@
    $output = & $PythonPath -c $check $LockFile 2>&1
    $code = $LASTEXITCODE
    return @{ Ok = ($code -eq 0); Detail = (($output | Out-String).Trim()) }
}

function Get-LocalPathSets {
    param([string]$RepoRoot)
    $status = & git @gitArgs status --porcelain=v1 -z --untracked-files=all --ignored=matching 2>$null
    if ($LASTEXITCODE -ne 0) {
        throw 'Unable to read local tracked/untracked/ignored state; rollback stopped.'
    }
    $files = @{}
    $dirs = @{}
    $ignoredDirs = @{}
    $records = (($status | Out-String) -split "`0")
    foreach ($record in $records) {
        if ($record.Length -lt 4) { continue }
        $code = $record.Substring(0, 2)
        if ($code -ne '??' -and $code -ne '!!') { continue }
        $path = $record.Substring(3).Replace('\', '/').TrimEnd('/')
        if (-not $path) { continue }
        $lower = $path.ToLowerInvariant()
        if ($record.Substring(3).EndsWith('/')) {
            $dirs[$lower] = $true
            if ($code -eq '!!') { $ignoredDirs[$lower] = $true }
        } else {
            $files[$lower] = $true
        }
        $parts = $lower -split '/'
        for ($i = 1; $i -lt $parts.Length; $i++) {
            $ancestor = ($parts[0..($i - 1)] -join '/')
            $dirs[$ancestor] = $true
        }
    }
    return @{ Files = $files; Dirs = $dirs; IgnoredDirs = $ignoredDirs }
}

Write-Host '==== VEIL Offline Version Rollback ===='
Write-Host "Repository: $root"
Write-Host "Target ref: $TargetRef"

# 1. Refuse to run while VEIL desktop or chat service is running.
if (Get-Process -Name 'VEIL' -ErrorAction SilentlyContinue) {
    throw 'VEIL.exe is still running. Fully quit VEIL Desktop before rolling back.'
}
if (Test-SessionLockHeld -RepoRoot $root) {
    throw 'VEIL backend service holds data\session.lock. Quit VEIL before rolling back.'
}

# 2. Resolve the local target commit WITHOUT any network fetch/pull.
$gitArgs = @('-c', "safe.directory=$root", '-c', 'core.quotepath=false', '-C', $root)
$resolved = (& git @gitArgs rev-parse --verify --quiet "$TargetRef^{commit}" 2>$null)
if ($LASTEXITCODE -ne 0 -or -not $resolved) {
    throw "Local commit/ref '$TargetRef' not found. This rollback script never fetches from remote."
}
$resolved = $resolved.Trim()
$currentHead = (& git @gitArgs rev-parse HEAD).Trim()
$currentBranch = (& git @gitArgs branch --show-current)
if ($null -eq $currentBranch) { $currentBranch = '' }
$currentBranch = $currentBranch.Trim()
Write-Host "Current HEAD: $currentHead (branch: $(if ($currentBranch) { $currentBranch } else { 'detached' }))"
Write-Host "Resolved target: $resolved"

# 3. Refuse uncommitted tracked changes; the safety branch cannot recover those.
$dirty = & git @gitArgs status --porcelain --untracked-files=no
if ($dirty) {
    throw "Working tree has uncommitted tracked changes. Commit or stash them before rollback.`n$($dirty -join "`n")"
}

# 4. Refuse any untracked/ignored local path that the target commit would overwrite.
#    git reset --hard silently overwrites such files, so this check must run BEFORE the switch.
$targetFiles = @(& git @gitArgs ls-tree -r --name-only $resolved)
if ($LASTEXITCODE -ne 0) {
    throw "Unable to read the target commit tree for '$resolved'; rollback stopped."
}
$targetFiles = @($targetFiles | Where-Object { $_ -and $_.Trim() } | ForEach-Object { $_.Replace('\', '/') })
$local = Get-LocalPathSets -RepoRoot $root
$conflicts = New-Object System.Collections.Generic.List[string]
foreach ($target in $targetFiles) {
    $lower = $target.ToLowerInvariant()
    if ($local.Files.ContainsKey($lower)) {
        $conflicts.Add("$target (local untracked/ignored file would be overwritten)")
    }
    $parts = $lower -split '/'
    for ($i = 1; $i -lt $parts.Length; $i++) {
        $ancestor = ($parts[0..($i - 1)] -join '/')
        if ($local.Files.ContainsKey($ancestor)) {
            $conflicts.Add("$target (a local untracked/ignored file blocks a target directory)")
        }
        if ($local.IgnoredDirs.ContainsKey($ancestor)) {
            $conflicts.Add("$target (a local ignored directory may contain files at this path)")
        }
    }
    if ($local.Dirs.ContainsKey($lower)) {
        $conflicts.Add("$target (a local untracked/ignored directory would be replaced by a target file)")
    }
}
if ($conflicts.Count -gt 0) {
    $shown = ($conflicts | Select-Object -Unique | Select-Object -First 40) -join "`n"
    throw "Rollback stopped: local untracked/ignored paths conflict with the target commit.`n$shown`nMove or back up these files first; no file was changed."
}

# 5. Check dependencies before switching. Offline is the default; online sync needs explicit consent.
$py = Join-Path $root '.venv\Scripts\python.exe'
$desktopDir = Join-Path $root 'desktop'
$targetReq = (& git @gitArgs show ("{0}:requirements.lock.txt" -f $resolved) 2>$null)
$reqChanged = ($LASTEXITCODE -eq 0 -and $targetReq)
$pkgLockChanged = (& git @gitArgs diff --name-only $currentHead $resolved -- desktop/package-lock.json)
$nodeReady = ((Test-Path -LiteralPath (Join-Path $desktopDir 'node_modules\electron\package.json')) -and
              (Test-Path -LiteralPath (Join-Path $desktopDir 'node_modules\@electron\packager')))
$needsPythonSync = $false
$needsNodeSync = $false

if (-not $SkipPythonDeps -and -not (Test-Path -LiteralPath $py)) {
    throw 'Offline rollback stopped: .venv\\Scripts\\python.exe is missing. Run setup.bat first, or use -SkipPythonDeps for a code-only rollback.'
}

if (-not $SkipPythonDeps -and $reqChanged) {
    $tempLock = [System.IO.Path]::GetTempFileName()
    try {
        Set-Content -LiteralPath $tempLock -Value ($targetReq -join "`n") -Encoding UTF8
        $pythonDeps = Test-PythonDependencies -PythonPath $py -LockFile $tempLock
    } finally {
        Remove-Item -LiteralPath $tempLock -ErrorAction SilentlyContinue
    }
    if (-not $pythonDeps.Ok) {
        if (-not $AllowOnlineDeps) {
            throw "Offline rollback stopped: target requirements are not installed in .venv.`n$($pythonDeps.Detail)`nRun again with -AllowOnlineDeps to authorize a network dependency sync, or -SkipPythonDeps to proceed without it."
        }
        $needsPythonSync = $true
        Write-Host 'Target Python dependencies are not installed; -AllowOnlineDeps was given.'
    }
}

if (-not $SkipPackage -and ($pkgLockChanged -or -not $nodeReady)) {
    if (-not $AllowOnlineDeps) {
        throw 'Offline rollback stopped: desktop dependencies changed or are missing. Run again with -AllowOnlineDeps to authorize a network dependency sync, or -SkipPackage to skip packaging.'
    }
    $needsNodeSync = $true
}

if ($needsPythonSync -and -not (Test-Path -LiteralPath $py)) {
    throw 'Cannot sync Python dependencies: .venv\\Scripts\\python.exe is missing; rollback stopped before changing files.'
}

if (-not $SkipPackage) {
    if (-not (Get-Command node -ErrorAction SilentlyContinue) -or -not (Get-Command npm -ErrorAction SilentlyContinue)) {
        throw 'node/npm are not available on PATH; rollback stopped before changing files.'
    }
    if (-not $AllowOnlineDeps) {
        if (-not $nodeReady) {
            throw 'Offline rollback stopped: desktop/node_modules is missing.'
        }
        Push-Location $desktopDir
        try {
            $cacheCheck = (& node package-desktop.cjs --check 2>&1 | Out-String).Trim()
            if ($LASTEXITCODE -ne 0) {
                throw "Offline rollback stopped: $cacheCheck Run again with -AllowOnlineDeps to authorize the Electron download."
            }
        } finally {
            Pop-Location
        }
    }
}

# 6. Create local safety recovery branch before moving HEAD.
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$backupBranch = "backup/before-rollback-$stamp"
& git @gitArgs branch $backupBranch $currentHead
if ($LASTEXITCODE -ne 0) {
    throw "Failed to create local safety branch $backupBranch."
}
Write-Host "Created local safety branch: $backupBranch -> $currentHead"

# 7. Roll back working tree to the target commit. The preflight above protects local files.
if ($currentBranch) {
    & git @gitArgs reset --hard $resolved
} else {
    & git @gitArgs checkout --detach $resolved
}
if ($LASTEXITCODE -ne 0) {
    throw "Git rollback to $resolved failed. Safety branch preserved at $backupBranch."
}
$switched = $true
Write-Host "Working tree rolled back to $resolved."

try {

# 8. Sync dependencies only when the user explicitly allowed network access.
if ($needsPythonSync) {
    if (-not (Test-Path -LiteralPath $py)) {
        throw 'Cannot sync Python dependencies: .venv\Scripts\python.exe is missing.'
    }
    Write-Host 'Syncing Python packages from requirements.lock.txt (network allowed by -AllowOnlineDeps)...'
    & $py -m pip install --no-cache-dir -r (Join-Path $root 'requirements.lock.txt')
    if ($LASTEXITCODE -ne 0) {
        throw "Python dependency sync failed; the previous code will be restored. Safety branch: $backupBranch"
    }
}

if ($needsNodeSync) {
    Push-Location $desktopDir
    try {
        Write-Host 'Installing desktop dependencies (network allowed by -AllowOnlineDeps)...'
        & npm ci --no-audit --no-fund
        if ($LASTEXITCODE -ne 0) { throw 'npm ci failed during rollback.' }
        & node node_modules/electron/install.js
        if ($LASTEXITCODE -ne 0) { throw 'electron install failed during rollback.' }
    } finally {
        Pop-Location
    }
}

# 9. Rebuild VEIL Desktop, preserving the previous dist until the new one is verified.
if (-not $SkipPackage) {
    $distDir = Join-Path $desktopDir 'dist'
    $backupRoot = Join-Path $root '.local-tmp'
    $distBackup = $null
    if (Test-Path -LiteralPath $distDir) {
        $item = Get-Item -LiteralPath $distDir
        if (-not $item.PSIsContainer) {
            throw 'desktop\dist exists but is not a directory; refusing to package.'
        }
        New-Item -ItemType Directory -Force -Path $backupRoot | Out-Null
        $distBackup = Join-Path $backupRoot ("rollback-dist-" + $stamp)
        Move-Item -LiteralPath $distDir -Destination $distBackup
        Write-Host "Preserved existing desktop build at $distBackup"
    }

    Push-Location $desktopDir
    $previousOffline = $env:VEIL_OFFLINE
    if (-not $AllowOnlineDeps) { $env:VEIL_OFFLINE = '1' }
    try {
        Write-Host "Packaging VEIL Desktop from rolled-back commit $resolved..."
        & npm run package
        if ($LASTEXITCODE -ne 0) { throw 'npm run package failed during rollback.' }

        $exePath = Join-Path $root 'desktop\dist\VEIL-win32-x64\VEIL.exe'
        if (-not (Test-Path -LiteralPath $exePath)) {
            throw "Expected packaged executable not found at $exePath."
        }
        $desktopRev = (& git @gitArgs rev-list -1 HEAD -- desktop).Trim()
        $stampPath = Join-Path $root 'desktop\dist\built-from.txt'
        Set-Content -LiteralPath $stampPath -Value $desktopRev -Encoding ascii
        Write-Host "Desktop app repackaged successfully (desktop rev: $desktopRev)."
        if ($distBackup) { Write-Host "Previous desktop build kept at $distBackup" }
    } catch {
        if ($distBackup -and (Test-Path -LiteralPath $distBackup)) {
            if (Test-Path -LiteralPath $distDir) {
                Remove-Item -LiteralPath $distDir -Recurse -Force
            }
            Move-Item -LiteralPath $distBackup -Destination $distDir
            Write-Warning "Packaging failed; the previous desktop build was restored to $distDir."
        }
        throw
    } finally {
        if ($null -eq $previousOffline) {
            Remove-Item Env:VEIL_OFFLINE -ErrorAction SilentlyContinue
        } else {
            $env:VEIL_OFFLINE = $previousOffline
        }
        Pop-Location
    }
}

} catch {
    $failureMessage = $_.Exception.Message
    $restoreNote = 'previous code was not changed'
    if ($switched) {
        if ($currentBranch) {
            & git @gitArgs reset --hard $currentHead
        } else {
            & git @gitArgs checkout --detach $currentHead
        }
        if ($LASTEXITCODE -eq 0) {
            $restoreNote = 'previous code restored'
        } else {
            $restoreNote = 'could not restore previous code; use the safety branch'
        }
    }
    throw "Rollback failed: $failureMessage`n$restoreNote. Safety branch: $backupBranch"
}

Write-Host "Rollback complete. Safety branch: $backupBranch"
Write-Host 'User data (data/, characters/, persona/, .env) was not touched.'
