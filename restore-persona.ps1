# Ticket 214: put an earlier saved copy of persona\peter.md back. Saves the current one first.
param([int]$Pick = 0)
$ErrorActionPreference = 'Stop'
$persona = Join-Path $PSScriptRoot 'persona\peter.md'
$folder = Join-Path $PSScriptRoot 'persona\versions'
if (-not (Test-Path -LiteralPath $folder)) { Write-Host '还没有保存过的人设版本（重开一次 Peter 后会自动保存当前版本）。'; exit 1 }
$pattern = '^peter\.(\d{8}-\d{6})(?:-(\d+))?\.md$'
$items = @(Get-ChildItem -LiteralPath $folder -Filter 'peter.*.md' -File |
    Where-Object { $_.Name -match $pattern } |
    Sort-Object @{ Expression = {
        $m = [regex]::Match($_.Name, $pattern)
        $n = if ($m.Groups[2].Success) { [int]$m.Groups[2].Value } else { -1 }
        '{0}|{1:D6}' -f $m.Groups[1].Value, ($n + 1)
    }; Descending = $true })
if ($items.Count -eq 0) { Write-Host '还没有保存过的人设版本（重开一次 Peter 后会自动保存当前版本）。'; exit 1 }
Write-Host '已保存的人设版本（时间为 UTC，编号 1 最新）：'
for ($i = 0; $i -lt $items.Count; $i++) {
    $stamp = $items[$i].Name -replace '^peter\.', '' -replace '\.md$', ''
    Write-Host ('{0,3}. {1}  {2} 字节' -f ($i + 1), $stamp, $items[$i].Length)
}
if ($Pick -eq 0) {
    $answer = Read-Host '输入要还原的编号（直接回车取消）'
    if (-not $answer) { Write-Host '没改任何东西。'; exit 0 }
    if (-not [int]::TryParse($answer, [ref]$Pick)) { Write-Host '不是数字，什么都没改。'; exit 1 }
}
if ($Pick -lt 1 -or $Pick -gt $items.Count) { Write-Host '编号不在列表里，什么都没改。'; exit 1 }
$chosen = $items[$Pick - 1]
if (Test-Path -LiteralPath $persona) {
$current = [System.IO.File]::ReadAllBytes($persona)
$sha = [System.Security.Cryptography.SHA256]::Create()
$currentHash = [BitConverter]::ToString($sha.ComputeHash($current))
$known = $false
foreach ($item in $items) {
    if ([BitConverter]::ToString($sha.ComputeHash([System.IO.File]::ReadAllBytes($item.FullName))) -eq $currentHash) { $known = $true; break }
}
if (-not $known) {
    $stampNow = (Get-Date).ToUniversalTime().ToString('yyyyMMdd-HHmmss')
    $saved = Join-Path $folder ('peter.' + $stampNow + '.md')
    for ($n = 1; Test-Path -LiteralPath $saved; $n++) { $saved = Join-Path $folder ('peter.' + $stampNow + '-' + $n + '.md') }
    $backupTmp = $saved + '.partial'
    [System.IO.File]::WriteAllBytes($backupTmp, $current)
    [System.IO.File]::Move($backupTmp, $saved)
    Write-Host ('还原前的当前人设已另存：' + $saved)
}
}
$tmp = $persona + '.restore-tmp'
Copy-Item -LiteralPath $chosen.FullName -Destination $tmp -Force
if (Test-Path -LiteralPath $persona) { [System.IO.File]::Replace($tmp, $persona, [NullString]::Value) } else { [System.IO.File]::Move($tmp, $persona) }
Write-Host ('已还原为：' + $chosen.Name)
Write-Host '请完全关闭并重新打开 Peter，下一句话开始生效。'
