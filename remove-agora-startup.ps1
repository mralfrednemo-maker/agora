$ErrorActionPreference = "Stop"

$startup = [Environment]::GetFolderPath("Startup")
$shortcutPath = Join-Path $startup "Agora Gateway.lnk"

if (Test-Path -LiteralPath $shortcutPath) {
    Remove-Item -LiteralPath $shortcutPath -Force
    Write-Host "Removed Agora startup shortcut:"
    Write-Host $shortcutPath
} else {
    Write-Host "Agora startup shortcut was not installed:"
    Write-Host $shortcutPath
}
