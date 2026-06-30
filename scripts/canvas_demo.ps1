<#
.SYNOPSIS
  Canvas demo helper for FibreOps — probe the live NOC console, then validate
  the decision-sanitiser fix locally. Designed to be run inside the Copilot
  Terminal canvas alongside the Browser canvas pointing at the live site.

.EXAMPLE
  ./scripts/canvas_demo.ps1            # probe live + run targeted tests
  ./scripts/canvas_demo.ps1 -Serve     # also serve the console locally on :8800
#>
param(
  [string]$LiveUrl = "https://fbreops-noc-gkrykk.azurewebsites.net",
  [switch]$Serve
)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

Write-Host "== 1. Live health ==" -ForegroundColor Cyan
curl.exe -s "$LiveUrl/healthz"; Write-Host ""

Write-Host "`n== 2. Inspect live decisions (look for garbled text) ==" -ForegroundColor Cyan
curl.exe -s "$LiveUrl/api/runs" |
  python -c "import sys,json; d=json.load(sys.stdin); [print(r['run_id'], repr([s.get('decision') for s in r['steps'] if 'decision' in s])) for r in d['runs']]"

Write-Host "`n== 3. Validate sanitiser fix ==" -ForegroundColor Cyan
python -m pytest tests/test_orchestrator.py tests/test_ui.py -q

if ($Serve) {
  Write-Host "`n== 4. Serving NOC console locally on http://localhost:8800 ==" -ForegroundColor Cyan
  python -m fibreops.demo ui --port 8800
}
