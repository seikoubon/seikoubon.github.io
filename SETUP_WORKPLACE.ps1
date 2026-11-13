$ErrorActionPreference = 'Stop'

Write-Host '=== Rakuten RSS workplace setup ==='
python --version
uv --version
git --version

Write-Host ''
Write-Host 'Creating/syncing uv environment...'
uv sync

Write-Host ''
Write-Host 'Environment ready.'
Write-Host 'Open MarketSpeed II and the Excel workbook before running Python monitors.'
