# Registers the two acts-importer Task Scheduler jobs on THIS machine.
# Run once (safe to re-run - /F overwrites). This is local dev setup only;
# it does NOT travel with the repo when moving to production - see
# README.md's Scheduling section for what to do there instead.
#
#   ./scripts/setup_windows_tasks.ps1

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

schtasks /Create /SC MONTHLY /D 1 /ST 02:00 `
    /TN "ActsImporter-Monthly" `
    /TR "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$root\scripts\run_monthly.ps1`"" `
    /F

schtasks /Create /SC MONTHLY /D 1 /M JAN,APR,JUL,OCT /ST 03:00 `
    /TN "ActsImporter-QuarterlyRefresh" `
    /TR "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$root\scripts\run_quarterly_refresh.ps1`"" `
    /F

Write-Host ""
Write-Host "Registered. Check with:"
Write-Host "  schtasks /Query /TN ActsImporter-Monthly /V /FO LIST"
Write-Host "  schtasks /Query /TN ActsImporter-QuarterlyRefresh /V /FO LIST"
Write-Host "Logs land in .\logs\monthly.log and .\logs\quarterly_refresh.log"
