param([Parameter(Mandatory = $true)][string]$RepositoryPath)

$root = [System.IO.Path]::GetFullPath($RepositoryPath)
if ($root.Length -gt [System.IO.Path]::GetPathRoot($root).Length) { $root = $root.TrimEnd('\') }
$oldTarget = Join-Path $root 'start-desktop.bat'
$script = Join-Path $root 'start-desktop.vbs'
$desktop = [Environment]::GetFolderPath('Desktop')
if (-not (Test-Path -LiteralPath $script) -or -not (Test-Path -LiteralPath $desktop)) { throw 'Desktop launcher path is unavailable.' }

$shell = New-Object -ComObject WScript.Shell
Get-ChildItem -LiteralPath $desktop -Filter '*.lnk' -File -ErrorAction Stop | ForEach-Object {
    $shortcut = $shell.CreateShortcut($_.FullName)
    if ($shortcut.TargetPath -and [System.IO.Path]::GetFullPath($shortcut.TargetPath) -ieq $oldTarget) {
        $shortcut.TargetPath = Join-Path $env:SystemRoot 'System32\wscript.exe'
        $shortcut.Arguments = '"' + $script + '"'
        $shortcut.WorkingDirectory = $root
        $shortcut.WindowStyle = 7
        $shortcut.Save()
    }
}
