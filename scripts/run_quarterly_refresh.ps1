# Quarterly acts-importer refresh - re-syncs acts already stored too, to
# pick up amendments/repeals to existing acts (the monthly run's default
# skip-existing behavior never catches these). Expensive: walks the full
# ~11,463-item India Code ACT collection again, same as the very first
# backfill. Intended for a scheduler - never run this by hand unless testing.

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
New-Item -ItemType Directory -Force -Path (Join-Path $root "logs") | Out-Null
$log = Join-Path $root "logs\quarterly_refresh.log"

"[$(Get-Date -Format o)] Starting quarterly --refresh run" | Out-File -Append -Encoding utf8 $log

try {
    & "$root\venv\Scripts\python.exe" "$root\manage.py" import_acts --refresh *>> $log
    "[$(Get-Date -Format o)] Quarterly refresh finished (exit $LASTEXITCODE)" | Out-File -Append -Encoding utf8 $log
} catch {
    "[$(Get-Date -Format o)] Quarterly refresh FAILED: $_" | Out-File -Append -Encoding utf8 $log
    throw
}
