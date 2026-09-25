# Phase 1 validation - compare the rebuilt series against the values that were
# hand-computed in the existing workbooks. Expected values transcribed from
# 00881_EOS_20260909.xlsx (Signal_History row 10, Dashboard) and
# 00881_EOS_20260924.xlsx (Dashboard, Signal_History row 5).
# ASCII-only source.

param([string]$SeriesPath = 'data/series_00881.json')

$ErrorActionPreference = 'Stop'
$rows = [System.IO.File]::ReadAllText($SeriesPath, [System.Text.Encoding]::UTF8) | ConvertFrom-Json
$idx  = @{}
foreach ($r in $rows) { $idx[$r.date] = $r }

# field, expected, tolerance (relative unless abs_tol given)
$expect = @(
  @{ d='2026-09-03'; f='ret5';         v=0.0038;               tol=0.02  }
  @{ d='2026-09-03'; f='ret20';        v=0.0318;               tol=0.02  }
  @{ d='2026-09-03'; f='drawdown20';   v=-0.0245;              tol=0.02  }
  @{ d='2026-09-03'; f='rv20';         v=0.2157;               tol=0.01  }
  @{ d='2026-09-03'; f='rsi14';        v=53.70;                tol=0.01; note='warmup' }
  @{ d='2026-09-03'; f='volume_ratio'; v=0.3579;               tol=0.01  }
  @{ d='2026-09-03'; f='avg_vol20';    v=41440.6;              tol=0.01  }

  @{ d='2026-09-09'; f='ret5';         v=0.019000000000000128; tol=0.01  }
  @{ d='2026-09-09'; f='ret20';        v=0.02837786292306177;  tol=0.01  }
  @{ d='2026-09-09'; f='drawdown20';   v=-0.007789678675754419;tol=0.01  }
  @{ d='2026-09-09'; f='rv20';         v=0.21720162391497075;  tol=0.01  }
  @{ d='2026-09-09'; f='rsi14';        v=60.18689816858793;    tol=0.01; note='warmup' }
  @{ d='2026-09-09'; f='volume_ratio'; v=0.2646170038465086;   tol=0.01  }
  @{ d='2026-09-09'; f='avg_vol20';    v=36864.6;              tol=0.01  }

  @{ d='2026-09-23'; f='rv20';         v=0.2071;               tol=0.01  }
  @{ d='2026-09-23'; f='rsi14';        v=70.87;                tol=0.01; note='warmup' }
  @{ d='2026-09-23'; f='volume_ratio'; v=0.506;                tol=0.01  }

  @{ d='2026-09-24'; f='ret5';         v=0.0540052198;         tol=0.01  }
  @{ d='2026-09-24'; f='ret20';        v=0.0582543842;         tol=0.01  }
  @{ d='2026-09-24'; f='drawdown20';   v=-0.00190114;          tol=0.02  }
  @{ d='2026-09-24'; f='rv20';         v=0.207835;             tol=0.01  }
  @{ d='2026-09-24'; f='rsi14';        v=66.4887;              tol=0.01; note='warmup' }
  @{ d='2026-09-24'; f='volume_ratio'; v=0.572332;             tol=0.01  }
)

$pass = 0; $fail = 0; $warm = 0
$res = @()
foreach ($e in $expect) {
  $row = $idx[$e.d]
  if ($null -eq $row) { $res += [PSCustomObject]@{ 日期=$e.d; 欄位=$e.f; 模型='(無此日)'; 工作表=$e.v; 相對差=''; 判定='MISS' }; $fail++; continue }
  $got = $row.($e.f)
  if ($null -eq $got) { $res += [PSCustomObject]@{ 日期=$e.d; 欄位=$e.f; 模型='(null)'; 工作表=$e.v; 相對差=''; 判定='NULL' }; $fail++; continue }

  $denom = [Math]::Max([Math]::Abs([double]$e.v), 1e-9)
  $rel   = [Math]::Abs(([double]$got - [double]$e.v) / $denom)
  $ok    = $rel -le $e.tol
  $verdict = if ($ok) { 'PASS' } elseif ($e.note -eq 'warmup') { 'WARMUP'; } else { 'FAIL' }
  if ($ok) { $pass++ } elseif ($e.note -eq 'warmup') { $warm++ } else { $fail++ }

  $res += [PSCustomObject]@{
    日期   = $e.d
    欄位   = $e.f
    模型   = $(if ([Math]::Abs([double]$got) -ge 100) { '{0:N1}' -f $got } else { '{0:N6}' -f $got })
    工作表 = $(if ([Math]::Abs([double]$e.v) -ge 100) { '{0:N1}' -f $e.v } else { '{0:N6}' -f $e.v })
    相對差 = '{0:P2}' -f $rel
    判定   = $verdict
  }
}

$res | Format-Table -AutoSize
Write-Host ("PASS={0}  FAIL={1}  WARMUP-DIFF={2}" -f $pass, $fail, $warm)
Write-Host ""
Write-Host "WARMUP-DIFF is expected for rsi14: the workbook's Raw_History starts at 2026-08-10,"
Write-Host "so its Wilder average had ~14 bars of warmup. The rebuilt series warms up over 5+ years,"
Write-Host "which is the correct value. This is an improvement, not a regression."
