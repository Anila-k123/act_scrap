# Monthly acts-importer run - picks up newly published acts only.
# Cheap: skips every act already stored (no --refresh), so this never
# re-fetches the ~1,250 acts already imported, just whatever's new since
# last run. Intended for a scheduler (Windows Task Scheduler locally;
# cron/whatever equivalent in production) - never run this by hand unless
# testing.

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
New-Item -ItemType Directory -Force -Path (Join-Path $root "logs") | Out-Null
$log = Join-Path $root "logs\monthly.log"

"[$(Get-Date -Format o)] Starting monthly import_acts run" | Out-File -Append -Encoding utf8 $log

try {
    & "$root\venv\Scripts\python.exe" "$root\manage.py" import_acts *>> $log
    "[$(Get-Date -Format o)] Monthly run finished (exit $LASTEXITCODE)" | Out-File -Append -Encoding utf8 $log
} catch {
    "[$(Get-Date -Format o)] Monthly run FAILED: $_" | Out-File -Append -Encoding utf8 $log
    throw
}
