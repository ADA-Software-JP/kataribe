# Starts Kataribe (the console is minimized). (Kataribe を起動する)
Set-Location -LiteralPath $PSScriptRoot
$py = Get-Command python -ErrorAction SilentlyContinue
if ($py) {
    Start-Process -FilePath $py.Source -ArgumentList "server.py" -WindowStyle Minimized
} elseif (Get-Command py -ErrorAction SilentlyContinue) {
    Start-Process -FilePath "py" -ArgumentList "-3", "server.py" -WindowStyle Minimized
} else {
    Write-Host "Python 3 was not found. Install it from https://www.python.org/ (Python 3 が見つかりません)"
    Read-Host "Press Enter to exit (Enter で終了)"
}
