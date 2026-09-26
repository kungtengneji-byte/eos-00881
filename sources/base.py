"""HTTP 取數的共同基礎。

兩件事是刻意的設計決定，不要「優化」掉：

1. 偵測到機器人驗證（proof-of-work、CAPTCHA、Imperva challenge 之類）時，
   一律丟 BotChallengeDetected 並讓該欄位變成 UNAVAILABLE。
   平台不繞過這類機制 —— Stooq 與台灣銀行都在 2026-09 加上了這種驗證，
   正確的反應是換來源或回報缺值，不是去解題。

2. 所有 parse 函式與 fetch 函式分離，parse 只吃已解析的 dict。
   這樣測試可以完全用 tests/fixtures/ 的真實回應跑，不需要網路。
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime
from typing import Any

USER_AGENT = "Mozilla/5.0 (compatible; eos-tracker/1.0)"
DEFAULT_TIMEOUT = 45


class SourceError(Exception):
    """來源不可用的基底類別。"""


class BotChallengeDetected(SourceError):
    """來源回了機器人驗證頁。絕不嘗試通過，直接標記來源不可用。"""


class UnexpectedPayload(SourceError):
    """回應結構與預期契約不符 —— 通常代表來源改版，需要修 adapter。"""


# 機器人驗證頁的特徵字串（實際踩到的：Stooq 的 PoW、台銀的 Imperva）
_CHALLENGE_MARKERS = (
    "requires javascript to verify your browser",
    "challenge validation",
    "crypto.subtle.digest",
    "_incapsula_",
    "__verify",
    "cf-browser-verification",
    "captcha",
)


def _looks_like_challenge(text: str) -> bool:
    head = text[:4000].lower()
    if "<html" not in head and "<!doctype" not in head:
        return False
    return any(m in head for m in _CHALLENGE_MARKERS)


def fetch_text(url: str, *, timeout: int = DEFAULT_TIMEOUT, retries: int = 2,
               backoff: float = 3.0, data: dict[str, str] | None = None,
               encoding: str = "utf-8") -> str:
    """取回文字內容。遇到機器人驗證直接放棄，不重試也不繞過。

    data 不為 None 時改走 POST（期交所的下載端點只接受 POST）。
    encoding 供非 UTF-8 來源使用 —— 期交所的 CSV 是 Big5。
    """
    body = urllib.parse.urlencode(data).encode("ascii") if data is not None else None
    last_err: Exception | None = None
    for attempt in range(retries + 1):
        try:
            headers = {"User-Agent": USER_AGENT}
            if body is not None:
                headers["Content-Type"] = "application/x-www-form-urlencoded"
            req = urllib.request.Request(url, data=body, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
            text = raw.decode(encoding, errors="replace")
            if _looks_like_challenge(text):
                raise BotChallengeDetected(
                    f"{url} 回應為機器人驗證頁，此來源已不可自動取得，需改用替代來源"
                )
            return text
        except BotChallengeDetected:
            raise
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
            last_err = exc
            if attempt < retries:
                time.sleep(backoff * (attempt + 1))
    raise SourceError(f"{url} 取得失敗：{last_err}")


def fetch_json(url: str, **kw: Any) -> Any:
    text = fetch_text(url, **kw)
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise UnexpectedPayload(f"{url} 回應非合法 JSON：{exc}") from exc


# ---------------------------------------------------------------- 數值/日期

def to_float(raw: Any) -> float | None:
    """TWSE 的數字帶千分位逗號，缺值以 '--' / 'X0.00' 表示。"""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    s = str(raw).strip().replace(",", "").replace("%", "")
    if s in ("", "--", "---", "X0.00", "N/A", "null"):
        return None
    s = s.lstrip("+")
    if s.startswith("X"):          # TWSE 用 X 前綴標記除權息當日的價差
        s = s[1:]
    try:
        return float(s)
    except ValueError:
        return None


def roc_to_date(raw: str) -> date:
    """民國日期轉西元。接受 '115/09/01' 與 '115年09月01日' 兩種格式。"""
    digits: list[str] = []
    cur = ""
    for ch in str(raw):
        if ch.isdigit():
            cur += ch
        elif cur:
            digits.append(cur)
            cur = ""
    if cur:
        digits.append(cur)
    if len(digits) < 3:
        raise UnexpectedPayload(f"無法解析民國日期：{raw!r}")
    y, m, d = int(digits[0]) + 1911, int(digits[1]), int(digits[2])
    return date(y, m, d)


def slash_to_date(raw: str) -> date:
    """'2026/09/24' -> date。國泰 API 用這個格式。"""
    parts = str(raw).strip().split("/")
    if len(parts) != 3:
        raise UnexpectedPayload(f"無法解析日期：{raw!r}")
    return date(int(parts[0]), int(parts[1]), int(parts[2]))


def rows_of(payload: dict[str, Any], *, url: str) -> list[list[Any]]:
    """取出 TWSE 回應的 data 陣列，並檢查 stat。"""
    if not isinstance(payload, dict):
        raise UnexpectedPayload(f"{url} 回應不是物件")
    stat = payload.get("stat")
    if stat is not None and stat != "OK":
        # 上市前的月份、休市日查詢都會走到這裡，屬正常情況而非錯誤
        return []
    data = payload.get("data")
    if data is None:
        return []
    if not isinstance(data, list):
        raise UnexpectedPayload(f"{url} 的 data 不是陣列")
    return data


def utcnow() -> datetime:
    return datetime.now()
