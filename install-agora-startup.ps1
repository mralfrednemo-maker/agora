$ErrorActionPreference = "Stop"

$startup = [Environment]::GetFolderPath("Startup")
$shortcutPath = Join-Path $startup "Agora Gateway.lnk"
$target = "C:\Users\chris\PROJECTS\agora\start-agora.bat"
$workingDirectory = "C:\Users\chris\PROJECTS\agora"

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $target
$shortcut.WorkingDirectory = $workingDirectory
$shortcut.WindowStyle = 7
$shortcut.Description = "Start local Agora gateway on Windows login"
$shortcut.Save()

Write-Host "Installed Agora startup shortcut:"
Write-Host $shortcutPath
