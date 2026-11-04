# Ticket 212: remove the "voice rules" block from persona\peter.md. Saves a copy first; touches nothing else.
$ErrorActionPreference = 'Stop'
$persona = Join-Path $PSScriptRoot 'persona\peter.md'
$begin = '<!-- VEIL-VOICE-RULES-BEGIN -->'
$end = '<!-- VEIL-VOICE-RULES-END -->'
if (-not (Test-Path -LiteralPath $persona)) { Write-Host "找不到 $persona，什么都没改。"; exit 1 }
$utf8 = New-Object System.Text.UTF8Encoding($false)
$text = [System.IO.File]::ReadAllText($persona, $utf8)
$pattern = '(?s)(\r?\n)*' + [regex]::Escape($begin) + '.*?' + [regex]::Escape($end) + '(\r?\n)*'
$hasMarker = $text.Contains($begin) -or $text.Contains($end)
if ($text -notmatch $pattern) {
    if ($hasMarker) { Write-Host '人设里只找到“说话规则”块的开头或结尾标记，不完整，什么都没改。请手动检查 persona\peter.md。'; exit 1 }
    Write-Host '人设里没有找到“说话规则”块（可能已经撤回过），什么都没改。'; exit 0
}
$copy = Join-Path (Split-Path $persona) ('peter.before-undo-voice-rules-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.md')
Copy-Item -LiteralPath $persona -Destination $copy
$nl = if ($text.Contains("`r`n")) { "`r`n" } else { "`n" }
$new = $text
while ($true) {
    $m = [regex]::Match($new, $pattern)
    if (-not $m.Success) { break }
    $before = $new.Substring(0, $m.Index)
    $after = $new.Substring($m.Index + $m.Length)
    $sep = if ($before.Length -gt 0 -and $after.Length -gt 0) { $nl + $nl } elseif ($before.Length -gt 0) { $nl } else { '' }
    $new = $before + $sep + $after
}
if ($new.Contains($begin) -or $new.Contains($end)) { Write-Host '撤回后人设里仍残留规则标记，不完整，什么都没改。请手动检查 persona\peter.md。'; exit 1 }
$tmp = $persona + '.undo-tmp'
[System.IO.File]::WriteAllText($tmp, $new, $utf8)
[System.IO.File]::Replace($tmp, $persona, [NullString]::Value)
Write-Host '已撤回“说话规则”块。撤回前的人设已另存：'
Write-Host $copy
Write-Host '请完全关闭并重新打开 Peter，下一句话开始生效。'
