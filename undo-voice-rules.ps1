# Ticket 212: remove the "voice rules" block from persona\peter.md. Saves a copy first; touches nothing else.
$ErrorActionPreference = 'Stop'
$persona = Join-Path $PSScriptRoot 'persona\peter.md'
$begin = '<!-- VEIL-VOICE-RULES-BEGIN -->'
$end = '<!-- VEIL-VOICE-RULES-END -->'
if (-not (Test-Path -LiteralPath $persona)) { Write-Host "找不到 $persona，什么都没改。"; exit 1 }
$utf8 = New-Object System.Text.UTF8Encoding($false)
$text = [System.IO.File]::ReadAllText($persona, $utf8)
$pattern = '(?s)(\r?\n)*' + [regex]::Escape($begin) + '.*?' + [regex]::Escape($end) + '(\r?\n)*'
if ($text -notmatch $pattern) { Write-Host '人设里没有找到“说话规则”块（可能已经撤回过），什么都没改。'; exit 0 }
$copy = Join-Path (Split-Path $persona) ('peter.before-undo-voice-rules-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.md')
Copy-Item -LiteralPath $persona -Destination $copy
$nl = if ($text.Contains("`r`n")) { "`r`n" } else { "`n" }
$new = [regex]::Replace($text, $pattern, $nl)
[System.IO.File]::WriteAllText($persona, $new.TrimEnd() + $nl, $utf8)
Write-Host '已撤回“说话规则”块。撤回前的人设已另存：'
Write-Host $copy
Write-Host '请完全关闭并重新打开 Peter，下一句话开始生效。'
