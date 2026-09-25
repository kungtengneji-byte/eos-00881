# Phase 1 backfill - fetch raw TWSE data for one instrument.
# One-off job. Output is the artifact of record and gets committed to raw/.
# Resumable: months already on disk are skipped, so a rate-limited run can be re-run.
# ASCII-only source (Windows PowerShell 5.1 reads scripts as ANSI).

param(
  [string]$StockNo   = '00881',
  [int]$StartYear    = 2020,
  [int]$StartMonth   = 12,
  [int]$EndYear      = 2026,
  [int]$EndMonth     = 9,
  [int]$DelaySec     = 4,
  [string]$RawDir    = 'raw/twse'
)

$ErrorActionPreference = 'Stop'
$ProgressPreference    = 'SilentlyContinue'

$outDir = Join-Path $RawDir $StockNo
New-Item -ItemType Directory -Force $outDir | Out-Null
$utf8 = New-Object System.Text.UTF8Encoding($false)

function Save-Json($obj, $path) {
  [System.IO.File]::WriteAllText($path, ($obj | ConvertTo-Json -Depth 10 -Compress), $utf8)
}

# ---- daily bars, one call per month -------------------------------------
$cur  = Get-Date -Year $StartYear -Month $StartMonth -Day 1
$stop = Get-Date -Year $EndYear   -Month $EndMonth   -Day 1
$fetched = 0; $skipped = 0; $empty = 0; $failed = @()

while ($cur -le $stop) {
  $tag  = $cur.ToString('yyyyMM')
  $path = Join-Path $outDir "STOCK_DAY_$tag.json"

  if (Test-Path $path) {
    $skipped++
  } else {
    $url = "https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY?date=${tag}01&stockNo=$StockNo&response=json"
    try {
      $r = Invoke-RestMethod -Uri $url -TimeoutSec 45 -UserAgent 'Mozilla/5.0'
      if ($r.stat -ne 'OK') {
        # Before listing date TWSE returns a non-OK stat; record it so the gap is explicit.
        Write-Host ("  {0}  stat={1} (no data)" -f $tag, $r.stat)
        $empty++
      } else {
        Save-Json $r $path
        Write-Host ("  {0}  {1,3} rows" -f $tag, $r.data.Count)
        $fetched++
      }
    } catch {
      Write-Host ("  {0}  FAILED: {1}" -f $tag, $_.Exception.Message)
      $failed += $tag
    }
    Start-Sleep -Seconds $DelaySec
  }
  $cur = $cur.AddMonths(1)
}

Write-Host ""
Write-Host ("daily bars: fetched={0} skipped={1} empty={2} failed={3}" -f $fetched, $skipped, $empty, $failed.Count)
if ($failed.Count -gt 0) { Write-Host ("  failed months: " + ($failed -join ', ')) }

# ---- dividends, multi-year range works in one call ----------------------
$divPath = Join-Path $outDir 'EXRIGHT.json'
if (Test-Path $divPath) {
  Write-Host 'dividends: already on disk, skipped'
} else {
  $s = '{0}1201' -f $StartYear
  $e = '{0}1231' -f $EndYear
  $url = "https://www.twse.com.tw/rwd/zh/exRight/TWT49U?startDate=$s&endDate=$e&response=json"
  try {
    $r = Invoke-RestMethod -Uri $url -TimeoutSec 90 -UserAgent 'Mozilla/5.0'
    $hits = @($r.data | Where-Object { $_ -contains $StockNo })
    Save-Json @{ fields = $r.fields; data = $hits; range = "$s-$e" } $divPath
    Write-Host ("dividends: {0} events for {1}" -f $hits.Count, $StockNo)
    $hits | ForEach-Object { Write-Host ("  {0}  cash={1}  pre={2}  ref={3}" -f $_[0], $_[5], $_[3], $_[4]) }
  } catch {
    Write-Host ("dividends: FAILED: {0}" -f $_.Exception.Message)
  }
}
