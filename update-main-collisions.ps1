param(
    [Parameter(Mandatory = $true)]
    [string]$RepositoryPath,
    [string]$BaseRef = 'main',
    [string]$TargetRef = 'origin/main'
)

$ErrorActionPreference = 'Stop'
$utf8 = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8

try {
    $root = [System.IO.Path]::GetFullPath($RepositoryPath).TrimEnd('\', '/')
    $rootPrefix = $root + [System.IO.Path]::DirectorySeparatorChar
    $addedPaths = @(& git -c core.quotePath=false -C $root diff --name-only --diff-filter=A --no-renames $BaseRef $TargetRef)
    if ($LASTEXITCODE -ne 0) {
        [Console]::Error.WriteLine('Could not list files newly tracked by the target branch.')
        exit 2
    }

    $collisions = [System.Collections.Generic.List[string]]::new()
    foreach ($relativePath in $addedPaths) {
        if ([string]::IsNullOrWhiteSpace($relativePath)) {
            continue
        }

        $relativePath = $relativePath.Replace('/', [System.IO.Path]::DirectorySeparatorChar)
        $candidatePath = [System.IO.Path]::GetFullPath(
            [System.IO.Path]::Combine($root, $relativePath))
        if (-not $candidatePath.StartsWith($rootPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
            [Console]::Error.WriteLine('A target path resolved outside the repository; update stopped.')
            exit 2
        }

        $currentPath = $candidatePath
        while ($currentPath.StartsWith($rootPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
            $item = Get-Item -LiteralPath $currentPath -Force -ErrorAction SilentlyContinue
            if ($null -ne $item) {
                $isExactPath = [string]::Equals(
                    $currentPath, $candidatePath, [System.StringComparison]::OrdinalIgnoreCase)
                $isReparsePoint = ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0
                if ($isExactPath -or -not $item.PSIsContainer -or $isReparsePoint) {
                    $collisions.Add($relativePath.Replace([System.IO.Path]::DirectorySeparatorChar, '/'))
                    break
                }
            }

            $currentPath = Split-Path -Path $currentPath -Parent
        }
    }

    $collisions | Sort-Object -Unique
    exit 0
}
catch {
    [Console]::Error.WriteLine('Unable to inspect local path conflicts: ' + $_.Exception.Message)
    exit 2
}
