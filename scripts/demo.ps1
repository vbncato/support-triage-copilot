<#
.SYNOPSIS
  Reproducible demo for Windows PowerShell 5.1 (demo mode, no API key needed).

.DESCRIPTION
  Starts the Compose stack (unless -NoStart), waits for /healthz, then over HTTP:
    1. creates a 401 ticket, analyses it, shows the draft + sources, approves it;
    2. creates an ambiguous ticket, gets a request for missing details, edits and approves it;
    3. creates an out-of-scope ticket and shows it routed to manual review;
    4. shows that a stale operator action gets HTTP 409.
  New tickets are created on every run, so the demo can be repeated without resetting the database.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\scripts\demo.ps1
#>
param(
  [string]$BaseUrl = "http://127.0.0.1:8091",
  [switch]$NoStart
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

function Find-Docker {
  $cmd = Get-Command docker -ErrorAction SilentlyContinue
  if ($cmd) { return $cmd.Source }
  $candidates = @(
    "$env:LOCALAPPDATA\Programs\DockerDesktop\resources\bin\docker.exe",
    "$env:ProgramFiles\Docker\Docker\resources\bin\docker.exe"
  )
  foreach ($c in $candidates) { if (Test-Path $c) { return $c } }
  throw "docker.exe not found. Start Docker Desktop and make sure docker is on PATH."
}

function Invoke-Api {
  param([string]$Method, [string]$Path, $Body = $null)
  $uri = "$BaseUrl$Path"
  try {
    if ($Body -ne $null) {
      $json = $Body | ConvertTo-Json -Depth 5
      $bytes = [System.Text.Encoding]::UTF8.GetBytes($json)
      $data = Invoke-RestMethod -Method $Method -Uri $uri -Body $bytes -ContentType "application/json; charset=utf-8" -TimeoutSec 90
    } else {
      $data = Invoke-RestMethod -Method $Method -Uri $uri -TimeoutSec 90
    }
    return @{ Status = 200; Data = $data }
  } catch [System.Net.WebException] {
    $resp = $_.Exception.Response
    if ($resp -eq $null) { throw }
    # PowerShell 5.1 has already read the error body into ErrorDetails.
    if ($_.ErrorDetails -and $_.ErrorDetails.Message) {
      $text = $_.ErrorDetails.Message
    } else {
      $reader = New-Object System.IO.StreamReader($resp.GetResponseStream(), [System.Text.Encoding]::UTF8)
      $text = $reader.ReadToEnd()
    }
    return @{ Status = [int]$resp.StatusCode; Data = ($text | ConvertFrom-Json) }
  }
}

function Write-Step([string]$Text) { Write-Host ""; Write-Host "== $Text" -ForegroundColor Cyan }

function Show-Analysis($d) {
  $t = $d.ticket
  $a = $d.current_attempt
  Write-Host ("  ticket #{0}  status={1}  version={2}" -f $t.id, $t.status, $t.version)
  if ($a -eq $null) { return }
  Write-Host ("  attempt {0}: state={1} mode={2} model={3}" -f $a.attempt_no, $a.state, $a.mode, $a.model)
  if ($a.error_type) { Write-Host ("  error: {0} - {1}" -f $a.error_type, $a.error_detail) -ForegroundColor Yellow }
  $p = $a.proposal
  if ($p -ne $null -and $a.state -eq "SUCCEEDED") {
    Write-Host ("  disposition={0}  category={1}  queue={2}" -f $p.disposition, $p.category, $p.queue)
    Write-Host ("  reason: {0}" -f $p.reason)
    if ($p.requested_details) { Write-Host ("  ask customer for: {0}" -f ($p.requested_details -join ", ")) }
    if ($p.source_ids) { Write-Host ("  cited sources: {0}" -f ($p.source_ids -join ", ")) }
    if ($p.draft_reply) {
      Write-Host "  draft:"
      ($p.draft_reply -split "`n") | ForEach-Object { Write-Host "    | $_" }
    }
  }
}

function New-DemoTicket([string]$Key) {
  $fixture = $script:fixtures | Where-Object { $_.external_id -eq $Key }
  $stamp = Get-Date -Format "yyyyMMddHHmmss"
  $r = Invoke-Api POST "/api/tickets" @{ subject = $fixture.subject; body = $fixture.body; external_id = "PS-$Key-$stamp" }
  if ($r.Status -ne 200) { throw "create failed: HTTP $($r.Status)" }
  Write-Host ("  created ticket #{0} ({1}): {2}" -f $r.Data.id, $r.Data.external_id, $r.Data.subject)
  return $r.Data
}

function Start-Analysis($Ticket, [int]$Version) {
  $r = Invoke-Api POST "/api/tickets/$($Ticket.id)/analysis" @{ expected_version = $Version }
  if ($r.Status -ne 200) { throw "analysis failed: HTTP $($r.Status) $($r.Data.error.code)" }
  return $r.Data
}

# --- start -----------------------------------------------------------------------------------
$docker = Find-Docker
Push-Location $root
try {
  if (-not $NoStart) {
    Write-Step "Starting Compose stack (demo mode)"
    & $docker compose up -d --build
    if ($LASTEXITCODE -ne 0) { throw "docker compose up failed" }
  }
  Write-Step "Waiting for $BaseUrl/healthz"
  $ok = $false
  for ($i = 0; $i -lt 60; $i++) {
    try { $h = Invoke-RestMethod -Uri "$BaseUrl/healthz" -TimeoutSec 3; $ok = $true; break } catch { Start-Sleep -Seconds 2 }
  }
  if (-not $ok) { throw "App did not become healthy" }
  Write-Host ("  status={0} database={1} mode={2} model={3}" -f $h.status, $h.database, $h.mode, $h.model)
  if ($h.mode -ne "demo") { Write-Host "  NOTE: not in demo mode; results below are live model output." -ForegroundColor Yellow }

  $script:fixtures = Get-Content -Raw -Encoding UTF8 (Join-Path $root "fixtures\tickets\demo-tickets.json") | ConvertFrom-Json

  # 1. 401 -> draft with source -> approve
  Write-Step "Scenario 1: 401 ticket -> draft reply with source -> approve"
  $t1 = New-DemoTicket "DEMO-001"
  $d1 = Start-Analysis $t1 1
  Show-Analysis $d1
  $src = Invoke-Api GET "/api/kb/kb-auth-401"
  Write-Host ("  source opens: /kb/{0} -> '{1}'" -f $src.Data.id, $src.Data.title)
  $r = Invoke-Api POST "/api/tickets/$($t1.id)/decisions" @{ action = "approve"; expected_version = $d1.ticket.version; attempt_id = $d1.current_attempt.id }
  Write-Host ("  approve -> HTTP {0}, status={1}, decision={2}" -f $r.Status, $r.Data.ticket.status, $r.Data.decision.action) -ForegroundColor Green
  $again = Invoke-Api POST "/api/tickets/$($t1.id)/decisions" @{ action = "approve"; expected_version = $d1.ticket.version; attempt_id = $d1.current_attempt.id }
  Write-Host ("  second click -> HTTP {0} {1}" -f $again.Status, $again.Data.error.code)

  # 2. ambiguous -> needs info -> edit and approve
  Write-Step "Scenario 2: ambiguous ticket -> request for missing details -> edit and approve"
  $t2 = New-DemoTicket "DEMO-002"
  $d2 = Start-Analysis $t2 1
  Show-Analysis $d2
  $edited = $d2.current_attempt.proposal.draft_reply + "`nIf it is easier, a screenshot of the error response also helps."
  $r = Invoke-Api POST "/api/tickets/$($t2.id)/decisions" @{ action = "edit_approve"; expected_version = $d2.ticket.version; attempt_id = $d2.current_attempt.id; final_text = $edited }
  Write-Host ("  edit_approve -> HTTP {0}, status={1}, edited={2}" -f $r.Status, $r.Data.ticket.status, $r.Data.decision.edited) -ForegroundColor Green

  # 3. out of scope -> manual review
  Write-Step "Scenario 3: question outside the knowledge base -> manual review"
  $t3 = New-DemoTicket "DEMO-003"
  $d3 = Start-Analysis $t3 1
  Show-Analysis $d3

  # 4. stale action -> 409
  Write-Step "Scenario 4: stale operator action -> HTTP 409"
  $t4 = New-DemoTicket "DEMO-008"
  $d4 = Start-Analysis $t4 1
  $d4b = Start-Analysis $t4 $d4.ticket.version
  $r = Invoke-Api POST "/api/tickets/$($t4.id)/decisions" @{ action = "approve"; expected_version = $d4.ticket.version; attempt_id = $d4.current_attempt.id }
  Write-Host ("  approve with old version {0} -> HTTP {1} {2}" -f $d4.ticket.version, $r.Status, $r.Data.error.code) -ForegroundColor Yellow
  Write-Host ("  message: {0}" -f $r.Data.error.message)

  Write-Step "Open in a browser"
  Write-Host "  Queue:            $BaseUrl/"
  Write-Host "  401 ticket:       $BaseUrl/tickets/$($t1.id)"
  Write-Host "  Needs-info:       $BaseUrl/tickets/$($t2.id)"
  Write-Host "  Manual review:    $BaseUrl/tickets/$($t3.id)"
  Write-Host "  API docs:         $BaseUrl/docs"
  Write-Host ""
  Write-Host "Demo finished. Stop with 'docker compose stop' (data is kept)."
} finally {
  Pop-Location
}
