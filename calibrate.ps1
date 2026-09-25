# EOS Rubric v1.1 校準腳本（過渡用；Phase 2 將以 eos/engine.py 重寫並讀同一份 YAML）
# 錨點資料全部取自 00881_EOS_20260909.xlsx 與 00881_EOS_20260924.xlsx 的 Signal_History / Dashboard。
# $null 代表該欄在原始檔案中即為空白，不做任何補值。

$ErrorActionPreference = 'Stop'

# Phase 1 wired in: every technical input now comes from the rebuilt TWSE series
# (data/series_00881.json) instead of the workbook transcription. Non-TWSE inputs
# (NAV/premium, Top10, US markets, VIX/US10Y/FX, institutional flows) stay as
# transcribed from the workbooks until Phase 2 collectors exist.
$seriesPath = 'data/series_00881.json'
$series = @{}
if (Test-Path $seriesPath) {
  $rows = [System.IO.File]::ReadAllText((Resolve-Path $seriesPath), [System.Text.Encoding]::UTF8) | ConvertFrom-Json
  for ($i = 0; $i -lt $rows.Count; $i++) {
    $r = $rows[$i]
    $series[$r.date] = @{
      row = $r
      dir = $(if ($i -gt 0) { if ($r.close -ge $rows[$i-1].close) { 'up' } else { 'down' } } else { $null })
    }
  }
  Write-Host ("series loaded: {0} bars, {1} .. {2}" -f $rows.Count, $rows[0].date, $rows[-1].date)
} else {
  Write-Host "WARNING: series not found, falling back to workbook-transcribed technicals"
}

$anchors = @(
  @{ date='2026-09-01'; actual=@{A=11;B=17;C=18;D=10;E=10;F=4};  eos=70
     premium=-0.0025; ddown=$null; rsi=$null; rv=$null; ma20=$null
     wcr=$null; breadth=$null
     sox=$null; tsm=$null; ndx=$null; nvda=$null
     vix=16.34; us10y=4.75; twd=31.633; twdPrev=$null
     volRatio=$null; inst=561.39; dir=$null }

  @{ date='2026-09-02'; actual=@{A=11;B=14;C=5;D=4;E=6;F=7};    eos=47
     premium=-0.0008; ddown=$null; rsi=$null; rv=$null; ma20=$null
     wcr=$null; breadth=2
     sox=0.0045; tsm=0.0036; ndx=0.0045; nvda=0.032
     vix=15.20; us10y=4.794; twd=31.728; twdPrev=31.633
     volRatio=$null; inst=$null; dir='down' }

  @{ date='2026-09-03'; actual=@{A=11;B=16;C=6;D=13;E=9;F=3};   eos=58
     premium=0.0044; ddown=-0.0245; rsi=53.70; rv=0.2157; ma20=$true
     wcr=-0.0031; breadth=3
     sox=0.0045; tsm=0.0036; ndx=0.0045; nvda=0.032
     vix=15.20; us10y=4.794; twd=31.755; twdPrev=31.728
     volRatio=0.3579; inst=-637.12; dir='down' }

  @{ date='2026-09-09'; actual=@{A=9;B=20;C=14;D=8;E=8;F=7};    eos=66
     premium=$null; ddown=-0.007789679; rsi=60.1869; rv=0.2172016; ma20=$true
     wcr=0.000970554; breadth=6
     sox=0.013; tsm=0.02355; ndx=-0.0032; nvda=-0.01975
     vix=15.33; us10y=4.789; twd=31.505; twdPrev=$null
     volRatio=0.264617; inst=211.25; dir='up' }

  @{ date='2026-09-21'; actual=@{A=10;B=21;C=17;D=13;E=8;F=8};  eos=77
     premium=$null; ddown=$null; rsi=$null; rv=$null; ma20=$null
     wcr=$null; breadth=$null
     sox=0.043; tsm=0.0102; ndx=0.0226; nvda=0.0134
     vix=14.87; us10y=4.95; twd=31.758; twdPrev=$null
     volRatio=$null; inst=470.38; dir='up' }

  @{ date='2026-09-22'; actual=@{A=12;B=22;C=16;D=14;E=10;F=9}; eos=83
     premium=$null; ddown=$null; rsi=$null; rv=$null; ma20=$null
     wcr=$null; breadth=$null
     sox=0.011; tsm=0.0154; ndx=0.004; nvda=0.0066
     vix=14.21; us10y=4.935; twd=31.708; twdPrev=31.758
     volRatio=$null; inst=608.2; dir='up' }

  @{ date='2026-09-23'; actual=@{A=13;B=22;C=16;D=13;E=11;F=8}; eos=83
     premium=-0.004542; ddown=$null; rsi=70.87; rv=0.2071; ma20=$null
     wcr=$null; breadth=$null
     sox=$null; tsm=0.0153; ndx=0.0045; nvda=-0.0147
     vix=15.18; us10y=5.106; twd=31.716; twdPrev=31.708
     volRatio=0.506; inst=389.21; dir='up' }

  @{ date='2026-09-24'; actual=@{A=10;B=21;C=14;D=6;E=5;F=4};   eos=60
     premium=$null; ddown=-0.00190114; rsi=66.4887; rv=0.207835; ma20=$true
     wcr=-0.00240006; breadth=6
     sox=-0.0123; tsm=0.0153; ndx=-0.0113; nvda=-0.0147
     vix=15.18; us10y=5.106; twd=31.78; twdPrev=31.716
     volRatio=0.572332; inst=-438.73; dir='down' }
)

# Phase 2：折溢價改用國泰投信官方 API（GetEtf30DaysNavAndPrice）的 diffRate，
# 取代工作表轉錄值。這是發行商自己算的折溢價，比市價/NAV 自行相除更權威。
$navPath = 'raw/cathay/00881_nav30_20260925.json'
$officialPremium = @{}
if (Test-Path $navPath) {
  $nav = [System.IO.File]::ReadAllText((Resolve-Path $navPath), [System.Text.Encoding]::UTF8) | ConvertFrom-Json
  foreach ($r in $nav.result) {
    $d = $r.date -replace '/', '-'
    if ($r.diffRate) { $officialPremium[$d] = [double]($r.diffRate -replace '%','') / 100 }
  }
  Write-Host ("official premium loaded: {0} days, {1} .. {2}" -f $officialPremium.Count,
    (($officialPremium.Keys | Sort-Object)[0]), (($officialPremium.Keys | Sort-Object)[-1]))
}
foreach ($a in $anchors) {
  if ($officialPremium.ContainsKey($a.date)) { $a.premium = $officialPremium[$a.date] }
}

# 用重建序列覆寫技術面輸入（含息調整、TWSE 官方量、5.8 年 RSI 暖身）
foreach ($a in $anchors) {
  if (-not $series.ContainsKey($a.date)) { continue }
  $r = $series[$a.date].row
  $a.ddown    = $r.drawdown20
  $a.rsi      = $r.rsi14
  $a.rv       = $r.rv20
  $a.volRatio = $r.volume_ratio
  $a.dir      = $series[$a.date].dir
  $a.ma20     = $r.close_adj_gt_ma20
  $a.ma20gt60 = $r.ma20_gt_ma60
  $a.ma120    = $r.close_adj_gt_ma120
  $a.ret60pos = $r.ret60_positive
}

# 依 bands 取分：bands 為 @(@{lt=上界; score=分}, ...)，lt=$null 代表最後一段
function Get-Band($value, $bands) {
  if ($null -eq $value) { return $null }
  foreach ($b in $bands) {
    if ($null -eq $b.lt) { return $b.score }
    if ($value -lt $b.lt) { return $b.score }
  }
  return $bands[-1].score
}

# 回傳 @{earned=?; avail=?}，缺值則 avail 不計入
function Add-Item($earned, $avail, $score, $max) {
  if ($null -eq $score) { return @{ earned=$earned; avail=$avail } }
  return @{ earned = $earned + $score; avail = $avail + $max }
}

$A1 = @(@{lt=-0.0050;score=15},@{lt=-0.0010;score=13},@{lt=0.0030;score=10},@{lt=0.0080;score=6},@{lt=$null;score=2})
$B2 = @(@{lt=0.010;score=4},@{lt=0.030;score=5},@{lt=0.080;score=7},@{lt=0.150;score=4},@{lt=$null;score=2})
$B3 = @(@{lt=30;score=2},@{lt=40;score=3},@{lt=55;score=4},@{lt=65;score=3},@{lt=75;score=2},@{lt=$null;score=1})
$B4 = @(@{lt=0.20;score=4},@{lt=0.25;score=3},@{lt=0.32;score=2},@{lt=$null;score=1})
$C1 = @(@{lt=-0.0060;score=0},@{lt=-0.0025;score=2},@{lt=-0.0005;score=4},@{lt=0.0005;score=6},@{lt=0.0025;score=8},@{lt=0.0060;score=10},@{lt=$null;score=12})
$C2 = @(@{lt=3;score=0},@{lt=5;score=2},@{lt=6;score=4},@{lt=7;score=5},@{lt=9;score=7},@{lt=$null;score=8})
$USp= @(@{lt=-0.015;score=0.00},@{lt=-0.005;score=0.25},@{lt=0.005;score=0.50},@{lt=0.015;score=0.80},@{lt=$null;score=1.00})
$E1 = @(@{lt=15;score=6},@{lt=18;score=5},@{lt=22;score=3},@{lt=28;score=2},@{lt=$null;score=1})
$E2 = @(@{lt=4.00;score=5},@{lt=4.50;score=4},@{lt=4.80;score=3},@{lt=5.00;score=2},@{lt=$null;score=1})
$E3 = @(@{lt=-0.0030;score=4},@{lt=0.0000;score=3},@{lt=0.0030;score=2},@{lt=$null;score=1})
$F1u= @(@{lt=0.50;score=1},@{lt=0.80;score=2},@{lt=1.20;score=4},@{lt=$null;score=5})
$F1d= @(@{lt=0.50;score=4},@{lt=0.80;score=3},@{lt=1.20;score=2},@{lt=$null;score=0})
$F2 = @(@{lt=-300;score=1},@{lt=-100;score=2},@{lt=100;score=3},@{lt=300;score=4},@{lt=$null;score=5})

function Score-Day($a) {
  $dim = [ordered]@{}

  # A
  $s = Get-Band $a.premium $A1
  $dim.A = @{ earned=$s; avail=$(if($null -eq $s){0}else{15}) }

  # B1 趨勢：checklist，僅 MA20 可得 → prorate
  $bE=0.0; $bA=0.0
  $chk = 0.0; $chkAvail = 0.0
  foreach ($c in @(@($a.ma20,3), @($a.ma20gt60,2), @($a.ma120,3), @($a.ret60pos,2))) {
    if ($null -ne $c[0]) { $chkAvail += $c[1]; if ($c[0]) { $chk += $c[1] } }
  }
  if ($chkAvail -gt 0) { $bE += ($chk/$chkAvail)*10; $bA += 10 }
  foreach ($p in @(@($a.ddown, $B2, 7, $true), @($a.rsi, $B3, 4, $false), @($a.rv, $B4, 4, $false))) {
    $v = $p[0]; if ($p[3] -and $null -ne $v) { $v = [Math]::Abs($v) }
    $sc = Get-Band $v $p[1]
    if ($null -ne $sc) { $bE += $sc; $bA += $p[2] }
  }
  $dim.B = @{ earned=$(if($bA -eq 0){$null}else{$bE}); avail=$bA }

  # C
  $cE=0.0; $cA=0.0
  foreach ($p in @(@($a.wcr,$C1,12), @($a.breadth,$C2,8))) {
    $sc = Get-Band $p[0] $p[1]; if ($null -ne $sc) { $cE += $sc; $cA += $p[2] }
  }
  $dim.C = @{ earned=$(if($cA -eq 0){$null}else{$cE}); avail=$cA }

  # D
  $dE=0.0; $dA=0.0
  foreach ($p in @(@($a.sox,5), @($a.tsm,4), @($a.ndx,3), @($a.nvda,3))) {
    $pct = Get-Band $p[0] $USp; if ($null -ne $pct) { $dE += $pct*$p[1]; $dA += $p[1] }
  }
  $dim.D = @{ earned=$(if($dA -eq 0){$null}else{$dE}); avail=$dA }

  # E
  $eE=0.0; $eA=0.0
  $twdChg = $null
  if ($null -ne $a.twd -and $null -ne $a.twdPrev) { $twdChg = $a.twd/$a.twdPrev - 1 }
  foreach ($p in @(@($a.vix,$E1,6), @($a.us10y,$E2,5), @($twdChg,$E3,4))) {
    $sc = Get-Band $p[0] $p[1]; if ($null -ne $sc) { $eE += $sc; $eA += $p[2] }
  }
  $dim.E = @{ earned=$(if($eA -eq 0){$null}else{$eE}); avail=$eA }

  # F
  $fE=0.0; $fA=0.0
  if ($null -ne $a.volRatio -and $null -ne $a.dir) {
    $bands = $(if ($a.dir -eq 'up') { $F1u } else { $F1d })
    $sc = Get-Band $a.volRatio $bands; $fE += $sc; $fA += 5
  }
  $sc = Get-Band $a.inst $F2; if ($null -ne $sc) { $fE += $sc; $fA += 5 }
  $dim.F = @{ earned=$(if($fA -eq 0){$null}else{$fE}); avail=$fA }

  $tE = 0.0; $tA = 0.0
  foreach ($k in $dim.Keys) { if ($null -ne $dim[$k].earned) { $tE += $dim[$k].earned; $tA += $dim[$k].avail } }
  $eos = if ($tA -eq 0) { $null } else { [Math]::Round($tE/$tA*100, 0) }
  return @{ dim=$dim; earned=$tE; avail=$tA; eos=$eos }
}

$rows = @()
foreach ($a in $anchors) {
  $r = Score-Day $a
  $fmt = { param($k)
    $e = $r.dim[$k].earned
    if ($null -eq $e) { "  --/--" } else { "{0,4:N1}/{1,-3}" -f $e, $r.dim[$k].avail } }
  $rows += [PSCustomObject][ordered]@{
    日期   = $a.date
    A      = (& $fmt 'A'); B = (& $fmt 'B'); C = (& $fmt 'C')
    D      = (& $fmt 'D'); E = (& $fmt 'E'); F = (& $fmt 'F')
    可得滿分 = $r.avail
    模型EOS = $r.eos
    人工EOS = $a.eos
    差異    = $(if ($null -eq $r.eos) { '' } else { $r.eos - $a.eos })
  }
}
$rows | Format-Table -AutoSize

"`n各構面差異（模型 - 人工，僅列可計分者）"
foreach ($a in $anchors) {
  $r = Score-Day $a
  $parts = @()
  foreach ($k in 'A','B','C','D','E','F') {
    $e = $r.dim[$k].earned
    if ($null -ne $e -and $r.dim[$k].avail -eq @{A=15;B=25;C=20;D=15;E=15;F=10}[$k]) {
      $d = $e - $a.actual[$k]
      $parts += ("{0} {1,5}" -f $k, $(if ($d -ge 0) { "+{0:N1}" -f $d } else { "{0:N1}" -f $d }))
    }
  }
  if ($parts.Count -gt 0) { "  $($a.date)  " + ($parts -join '  ') }
}
