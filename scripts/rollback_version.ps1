param(
    [Parameter(Mandatory = $true)]
    [string]$TargetRef,
    [string]$RepositoryPath = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path,
    [switch]$SkipPackage,
    [switch]$SkipPythonDeps
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

Write-Host "==== VEIL Offline Version Rollback ===="
Write-Host "Repository: $root"
Write-Host "Target ref: $TargetRef"

# 1. Refuse to run while VEIL desktop or chat service is running
if (Get-Process -Name 'VEIL' -ErrorAction SilentlyContinue) {
    throw 'VEIL.exe is still running. Fully quit VEIL Desktop before rolling back.'
}
if (Test-SessionLockHeld -RepoRoot $root) {
    throw 'VEIL backend service holds data\session.lock. Quit VEIL before rolling back.'
}

# 2. Verify git and local target commit WITHOUT any network fetch/pull
$gitArgs = @('-c', "safe.directory=$root", '-C', $root)
$resolved = (& git @gitArgs rev-parse --verify --quiet "$TargetRef^{commit}" 2>$null)
if ($LASTEXITCODE -ne 0 -or -not $resolved) {
    throw "Local commit/ref '$TargetRef' not found. This rollback script never fetches from remote."
}
$resolved = $resolved.Trim()
$currentHead = (& git @gitArgs rev-parse HEAD).Trim()
$currentBranch = (& git @gitArgs branch --show-current).Trim()

Write-Host "Current HEAD: $currentHead (branch: $(if ($currentBranch) { $currentBranch } else { 'detached' }))"
Write-Host "Resolved target: $resolved"

# 3. Check for uncommitted tracked changes
$dirty = & git @gitArgs status --porcelain --untracked-files=no
if ($dirty) {
    throw "Working tree has uncommitted tracked changes. Commit or stash them before rollback.`n$($dirty -join "`n")"
}

# 4. Create local safety recovery branch before moving HEAD
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$backupBranch = "backup/before-rollback-$stamp"
& git @gitArgs branch $backupBranch $currentHead
if ($LASTEXITCODE -ne 0) {
    throw "Failed to create local safety branch $backupBranch."
}
Write-Host "Created local safety branch: $backupBranch -> $currentHead"

# 5. Roll back working tree to target commit (never touches ignored user data)
if ($currentBranch) {
    & git @gitArgs reset --hard $resolved
} else {
    & git @gitArgs checkout --detach $resolved
}
if ($LASTEXITCODE -ne 0) {
    throw "Git rollback to $resolved failed. Safety branch preserved at $backupBranch."
}
Write-Host "Working tree rolled back to $resolved."

# 6. Optional Python dependency check (offline-friendly when wheel cache exists)
if (-not $SkipPythonDeps) {
    $py = Join-Path $root '.venv\Scripts\python.exe'
    $lockFile = Join-Path $root 'requirements.lock.txt'
    if ((Test-Path -LiteralPath $py) -and (Test-Path -LiteralPath $lockFile)) {
        $reqChanged = & git @gitArgs diff --name-only $currentHead $resolved -- requirements.lock.txt
        if ($reqChanged) {
            Write-Host "requirements.lock.txt differs from previous commit; syncing Python packages..."
            & $py -m pip install -q --no-cache-dir -r $lockFile
            if ($LASTEXITCODE -ne 0) {
                Write-Warning "Python dependency sync failed; rollback commit is active, check .venv manually."
            }
        }
    }
}

# 7. Rebuild VEIL Desktop locally from the rolled-back commit
if (-not $SkipPackage) {
    $desktopDir = Join-Path $root 'desktop'
    $pkgLockChanged = & git @gitArgs diff --name-only $currentHead $resolved -- desktop/package-lock.json
    $electronDir = Join-Path $desktopDir 'node_modules\electron'
    Push-Location $desktopDir
    try {
        if ($pkgLockChanged -or -not (Test-Path -LiteralPath $electronDir)) {
            Write-Host "Desktop dependencies changed or missing; running npm ci..."
            & npm ci --no-audit --no-fund
            if ($LASTEXITCODE -ne 0) { throw 'npm ci failed during rollback.' }
            & node node_modules/electron/install.js
            if ($LASTEXITCODE -ne 0) { throw 'electron install failed during rollback.' }
        }
        Write-Host "Packaging VEIL Desktop from rolled-back commit $resolved..."
        & npm run package
        if ($LASTEXITCODE -ne 0) { throw 'npm run package failed during rollback.' }
    } finally {
        Pop-Location
    }

    $exePath = Join-Path $root 'desktop\dist\VEIL-win32-x64\VEIL.exe'
    if (-not (Test-Path -LiteralPath $exePath)) {
        throw "Expected packaged executable not found at $exePath."
    }
    $desktopRev = (& git @gitArgs rev-list -1 HEAD -- desktop).Trim()
    $stampPath = Join-Path $root 'desktop\dist\built-from.txt'
    Set-Content -LiteralPath $stampPath -Value $desktopRev -Encoding ascii
    Write-Host "Desktop app repackaged successfully (desktop rev: $desktopRev)."
}

Write-Host "Rollback complete. Safety branch: $backupBranch"
Write-Host "User data (data/, characters/, persona/, .env) was not touched."
