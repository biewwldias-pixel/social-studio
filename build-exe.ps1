# Gera SocialStudio.exe localmente no Windows (PowerShell)
$ErrorActionPreference = "Stop"

uv sync
uv pip install pywebview pyinstaller
uv run pyinstaller `
  --noconfirm `
  --onefile `
  --windowed `
  --name SocialStudio `
  --icon "static/icon.ico" `
  --add-data "static;static" `
  --add-data "brands;brands" `
  --collect-all google.antigravity `
  desktop.py

Write-Host "Executável gerado em: dist\SocialStudio.exe"
