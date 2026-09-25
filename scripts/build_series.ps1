# Phase 1 - build the dividend-adjusted series and all B-dimension indicators
# from the raw TWSE bars fetched by fetch_twse.ps1.
#
# Formulas deliberately mirror the ones in Raw_History of 00881_EOS_20260909.xlsx
# so the output is comparable with the existing hand-built workbook:
#   ret      = (Close_t + Div_t) / Close_t-1 - 1        (xlsx col E)
#   TRI      = TRI_t-1 * (1 + ret_t), TRI_0 = 100       (xlsx col F)
#   adj      = Close_0 * TRI_t / TRI_0                  (xlsx col G)
#   retN     = PRODUCT(1 + ret over last N) - 1         (Signal_History E/F/G)
#   ddN      = adj_t / MAX(adj over last N) - 1         (Signal_History L/M)
#   RV20     = STDEV.S(ret over last 20) * SQRT(252)    (Signal_History N)
#   RSI14    = Wilder on diffs of adj                   (Raw_History K/L/M)
# ASCII-only source.

param(
  [string]$StockNo = '00881',
  [string]$RawDir  = 'raw/twse',
  [string]$OutDir  = 'data'
)

$ErrorActionPreference = 'Stop'
$inDir = Join-Path $RawDir $StockNo
New-Item -ItemType Directory -Force $OutDir | Out-Null
$utf8 = New-Object System.Text.UTF8Encoding($false)

# Windows PowerShell 5.1's ConvertTo-Json wraps nested object[] as
# {"value":[...],"Count":n}. Unwrap so both shapes load.
function Get-RowCells($row) {
  if ($row -is [psobject] -and ($row.PSObject.Properties.Name -contains 'value')) { return $row.value }
  return $row
}

function ConvertFrom-RocDate([string]$s) {
  # "115/09/01" or "115年09月01日"
  $m = [regex]::Match($s, '(\d+)\D+(\d+)\D+(\d+)')
  if (-not $m.Success) { throw "unparsable ROC date: $s" }
  $y = [int]$m.Groups[1].Value + 1911
  '{0:0000}-{1:00}-{2:00}' -f $y, [int]$m.Groups[2].Value, [int]$m.Groups[3].Value
}

function ConvertTo-Num([string]$s) {
  if ($null -eq $s) { return $null }
  $t = $s -replace '[,\s]', '' -replace '^\+', ''
  if ($t -in @('', '--', 'X0.00', '---')) { return $null }
  $t = $t -replace '^X', ''
  $o = 0.0
  if ([double]::TryParse($t, [ref]$o)) { return $o }
  return $null
}

# ---- load bars ----------------------------------------------------------
$bars = @{}
$files = @(Get-ChildItem (Join-Path $inDir 'STOCK_DAY_*.json') -ErrorAction SilentlyContinue)
if ($files.Count -eq 0) { throw "no raw bars in $inDir - run fetch_twse.ps1 first" }

foreach ($f in $files) {
  $j = [System.IO.File]::ReadAllText($f.FullName, [System.Text.Encoding]::UTF8) | ConvertFrom-Json
  foreach ($raw in $j.data) {
    $row = Get-RowCells $raw
    $d = ConvertFrom-RocDate $row[0]
    $bars[$d] = [ordered]@{
      date       = $d
      shares     = ConvertTo-Num $row[1]
      turnover   = ConvertTo-Num $row[2]
      open       = ConvertTo-Num $row[3]
      high       = ConvertTo-Num $row[4]
      low        = ConvertTo-Num $row[5]
      close      = ConvertTo-Num $row[6]
      trades     = ConvertTo-Num $row[8]
    }
  }
}

# ---- load dividends -----------------------------------------------------
$divs = @{}
$divPath = Join-Path $inDir 'EXRIGHT.json'
if (Test-Path $divPath) {
  $dj = [System.IO.File]::ReadAllText($divPath, [System.Text.Encoding]::UTF8) | ConvertFrom-Json
  foreach ($raw in $dj.data) {
    $row = Get-RowCells $raw
    $d = ConvertFrom-RocDate $row[0]
    $divs[$d] = ConvertTo-Num $row[5]
  }
}

$s = @($bars.Keys | Sort-Object | ForEach-Object { $bars[$_] })
Write-Host ("bars: {0}  range {1} .. {2}" -f $s.Count, $s[0].date, $s[-1].date)
Write-Host ("dividends applied: {0}" -f $divs.Count)

# ---- derived series -----------------------------------------------------
$n = $s.Count
$ret = New-Object 'double[]' $n
$tri = New-Object 'double[]' $n
$adj = New-Object 'double[]' $n
$hasRet = New-Object 'bool[]' $n

$tri[0] = 100.0
$adj[0] = $s[0].close
$base   = $s[0].close

for ($i = 1; $i -lt $n; $i++) {
  $d = 0.0
  if ($divs.ContainsKey($s[$i].date)) { $d = [double]$divs[$s[$i].date] }
  $ret[$i] = ($s[$i].close + $d) / $s[$i-1].close - 1
  $hasRet[$i] = $true
  $tri[$i] = $tri[$i-1] * (1 + $ret[$i])
  $adj[$i] = $base * $tri[$i] / 100.0
}

# Wilder RSI14 on adjusted-price diffs
$rsi = New-Object 'double[]' $n
$rsiOk = New-Object 'bool[]' $n
$P = 14
$ag = 0.0; $al = 0.0
for ($i = 1; $i -lt $n; $i++) {
  $ch = $adj[$i] - $adj[$i-1]
  $g = [Math]::Max($ch, 0.0); $l = [Math]::Max(-$ch, 0.0)
  if ($i -le $P) {
    $ag += $g / $P; $al += $l / $P
    if ($i -eq $P) { $rsi[$i] = $(if ($al -eq 0) { 100.0 } else { 100 - 100/(1 + $ag/$al) }); $rsiOk[$i] = $true }
  } else {
    $ag = ($ag * ($P-1) + $g) / $P
    $al = ($al * ($P-1) + $l) / $P
    $rsi[$i] = $(if ($al -eq 0) { 100.0 } else { 100 - 100/(1 + $ag/$al) })
    $rsiOk[$i] = $true
  }
}

function Get-MA($arr, $i, $w) {
  if ($i -lt $w-1) { return $null }
  $t = 0.0; for ($k = $i-$w+1; $k -le $i; $k++) { $t += $arr[$k] }
  return $t / $w
}
function Get-RetN($i, $w) {
  if ($i -lt $w) { return $null }
  $p = 1.0; for ($k = $i-$w+1; $k -le $i; $k++) { $p *= (1 + $ret[$k]) }
  return $p - 1
}
function Get-DrawdownN($i, $w) {
  if ($i -lt $w-1) { return $null }
  $mx = [double]::MinValue
  for ($k = $i-$w+1; $k -le $i; $k++) { if ($adj[$k] -gt $mx) { $mx = $adj[$k] } }
  return $adj[$i]/$mx - 1
}
function Get-RV($i, $w) {
  if ($i -lt $w) { return $null }
  $m = 0.0; for ($k = $i-$w+1; $k -le $i; $k++) { $m += $ret[$k] }
  $m /= $w
  $v = 0.0; for ($k = $i-$w+1; $k -le $i; $k++) { $v += [Math]::Pow($ret[$k]-$m, 2) }
  return [Math]::Sqrt($v/($w-1)) * [Math]::Sqrt(252)
}
function Get-AvgVolLots($i, $w) {
  # 20-day average INCLUDING today, matching xlsx AJ = AVERAGE(Raw_History!C5:C24)*1000
  # where row 24 is the current bar.
  if ($i -lt $w) { return $null }
  $t = 0.0; for ($k = $i-$w+1; $k -le $i; $k++) { $t += $s[$k].shares/1000.0 }
  return $t / $w
}

$out = @()
for ($i = 0; $i -lt $n; $i++) {
  $ma20  = Get-MA $adj $i 20
  $ma60  = Get-MA $adj $i 60
  $ma120 = Get-MA $adj $i 120
  $av20  = Get-AvgVolLots $i 20
  $lots  = $s[$i].shares/1000.0
  $ret60 = Get-RetN $i 60

  $out += [ordered]@{
    date          = $s[$i].date
    open          = $s[$i].open
    high          = $s[$i].high
    low           = $s[$i].low
    close         = $s[$i].close
    volume_lots   = [Math]::Round($lots, 0)
    turnover_100m = [Math]::Round($s[$i].turnover/1e8, 4)
    dividend      = $(if ($divs.ContainsKey($s[$i].date)) { $divs[$s[$i].date] } else { 0 })
    ret           = $(if ($hasRet[$i]) { $ret[$i] } else { $null })
    tri           = $tri[$i]
    close_adj     = $adj[$i]
    ma20          = $ma20
    ma60          = $ma60
    ma120         = $ma120
    ret5          = Get-RetN $i 5
    ret20         = Get-RetN $i 20
    ret60         = $ret60
    drawdown20    = Get-DrawdownN $i 20
    drawdown60    = Get-DrawdownN $i 60
    rsi14         = $(if ($rsiOk[$i]) { $rsi[$i] } else { $null })
    rv20          = Get-RV $i 20
    avg_vol20     = $av20
    volume_ratio  = $(if ($null -ne $av20 -and $av20 -gt 0) { $lots/$av20 } else { $null })
    # B1 checklist inputs - the whole point of Phase 1
    close_adj_gt_ma20  = $(if ($null -ne $ma20)  { $adj[$i] -gt $ma20 }  else { $null })
    ma20_gt_ma60       = $(if ($null -ne $ma20 -and $null -ne $ma60) { $ma20 -gt $ma60 } else { $null })
    close_adj_gt_ma120 = $(if ($null -ne $ma120) { $adj[$i] -gt $ma120 } else { $null })
    ret60_positive     = $(if ($null -ne $ret60) { $ret60 -gt 0 } else { $null })
  }
}

$jsonPath = Join-Path $OutDir "series_$StockNo.json"
[System.IO.File]::WriteAllText($jsonPath, ($out | ConvertTo-Json -Depth 5), $utf8)
Write-Host ("wrote {0}  ({1} rows)" -f $jsonPath, $out.Count)

$csvPath = Join-Path $OutDir "series_$StockNo.csv"
$out | ForEach-Object { [PSCustomObject]$_ } | Export-Csv -Path $csvPath -NoTypeInformation -Encoding UTF8
Write-Host ("wrote {0}" -f $csvPath)

# ---- coverage summary ---------------------------------------------------
$last = $out[-1]
Write-Host ""
Write-Host "latest bar: $($last.date)"
foreach ($k in 'close','close_adj','ma20','ma60','ma120','ret5','ret20','ret60','drawdown20','drawdown60','rsi14','rv20','volume_ratio') {
  $v = $last[$k]
  Write-Host ("  {0,-12} {1}" -f $k, $(if ($null -eq $v) { '(null)' } else { $v }))
}
