"""重抓測試 fixture，或僅檢查真實來源是否仍符合契約。

用途：
  python -m scripts.refresh_fixtures            # 重抓並覆寫 tests/fixtures/
  python -m scripts.refresh_fixtures --check    # 只檢查不寫檔（CI 每週排程用）

--check 的價值在於及早發現「來源加上機器人驗證」或「欄位改名」這類腐化。
Stooq 與台灣銀行都是在 2026-09 突然加上驗證的，沒有這道檢查就只會在
某天的收集結果裡安靜地出現一堆缺值。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sources.base import BotChallengeDetected, SourceError, fetch_text

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures"

# fixture 檔名 -> (URL, 用來確認結構仍正確的檢查函式)
TARGETS: dict[str, tuple[str, str]] = {
    "twse_stock_day_00881_202609.json": (
        "https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY"
        "?date=20260901&stockNo=00881&response=json", "twse_rows"),
    "twse_bfi82u_20260924.json": (
        "https://www.twse.com.tw/rwd/zh/fund/BFI82U"
        "?dayDate=20260924&type=day&response=json", "twse_rows"),
    "twse_t86_20260924.json": (
        "https://www.twse.com.tw/rwd/zh/fund/T86"
        "?date=20260924&selectType=ALLBUT0999&response=json", "twse_rows"),
    "twse_twt49u_2026.json": (
        "https://www.twse.com.tw/rwd/zh/exRight/TWT49U"
        "?startDate=20260101&endDate=20261231&response=json", "twse_rows"),
    "cathay_nav30_CR.json": (
        "https://cwapi.cathaysite.com.tw/api/ETF/GetEtf30DaysNavAndPrice?FundCode=CR",
        "cathay_result"),
    "cathay_weights_CR.json": (
        "https://cwapi.cathaysite.com.tw/api/ETF/GetIndexStockWeights?FundCode=CR",
        "cathay_weights"),
    "yahoo_chart_SOX.json": (
        "https://query1.finance.yahoo.com/v8/finance/chart/%5ESOX?interval=1d&range=10d",
        "yahoo_chart"),
    "yahoo_chart_TWDX.json": (
        "https://query1.finance.yahoo.com/v8/finance/chart/TWD=X?interval=1d&range=10d",
        "yahoo_chart"),
}


def check_twse_rows(payload) -> str | None:
    if not isinstance(payload, dict):
        return "回應不是物件"
    if "fields" not in payload or "data" not in payload:
        return "缺少 fields 或 data"
    return None


def check_cathay_result(payload) -> str | None:
    if not isinstance(payload, dict) or "success" not in payload:
        return "缺少 success 欄位"
    if not payload.get("success"):
        return f"success=False（{payload.get('returnMessage')}）"
    if not isinstance(payload.get("result"), list):
        return "result 不是陣列"
    return None


def check_cathay_weights(payload) -> str | None:
    if not isinstance(payload, dict) or not payload.get("success"):
        return f"success=False（{(payload or {}).get('returnMessage')}）"
    result = payload.get("result") or {}
    if "stockWeights" not in result or "date" not in result:
        return "result 缺少 stockWeights 或 date"
    return None


def check_yahoo_chart(payload) -> str | None:
    try:
        r = payload["chart"]["result"][0]
    except (KeyError, IndexError, TypeError):
        return "chart.result[0] 不存在"
    for key in ("meta", "timestamp", "indicators"):
        if key not in r:
            return f"缺少 {key}"
    if "regularMarketTime" not in r["meta"]:
        return "meta 缺少 regularMarketTime（判斷盤中所需）"
    return None


CHECKS = {
    "twse_rows": check_twse_rows,
    "cathay_result": check_cathay_result,
    "cathay_weights": check_cathay_weights,
    "yahoo_chart": check_yahoo_chart,
}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只檢查契約，不覆寫 fixture")
    args = ap.parse_args(argv)

    failures: list[str] = []
    for name, (url, check_key) in TARGETS.items():
        try:
            text = fetch_text(url)
        except BotChallengeDetected as exc:
            failures.append(f"{name}: 來源已加上機器人驗證，需改用替代來源 -> {exc}")
            continue
        except SourceError as exc:
            failures.append(f"{name}: 取得失敗 -> {exc}")
            continue

        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            failures.append(f"{name}: 回應非合法 JSON -> {exc}")
            continue

        problem = CHECKS[check_key](payload)
        if problem:
            failures.append(f"{name}: 契約不符 -> {problem}")
            continue

        if not args.check:
            (FIXTURES / name).write_text(text, encoding="utf-8")
            print(f"  updated {name} ({len(text)} bytes)")
        else:
            print(f"  ok      {name}")

    if failures:
        print("\n來源契約檢查失敗：", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        return 1
    print("\n所有來源契約正常")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
