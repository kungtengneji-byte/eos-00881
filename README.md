# 00881 EOS 每日追蹤平台

國泰台灣科技龍頭 ETF（00881）的 Entry Opportunity Score（EOS）自動化追蹤。
每交易日自動收集資料、計算六構面分數，並提供手機可瀏覽的歷史介面。

> **這不是投資建議。** EOS 是規則型研究分數，不是上漲機率 ——
> 80 分不代表「80% 會漲」。任何分數、訊號或統計結果都不構成投資建議、
> 報酬保證或價格承諾。

## 模型

EOS = 0.15A + 0.25B + 0.20C + 0.15D + 0.15E + 0.10F，滿分 100。

| 構面 | 權重 | 內容 |
|---|---:|---|
| A | 15 | ETF 折溢價（市價相對淨值） |
| B | 25 | 含息趨勢、回檔、RSI14、實現波動率 |
| C | 20 | Top10 成分股廣度與加權貢獻 |
| D | 15 | 海外科技（SOX、Nasdaq、TSM ADR、NVDA） |
| E | 15 | 風險環境（VIX、US10Y、USD/TWD） |
| F | 10 | 量價與法人資金流 |

評分門檻定義在 [`eos_rubric_v1.1.yaml`](eos_rubric_v1.1.yaml)。

## 資料來源

| 資料 | 來源 |
|---|---|
| 日線 OHLCV、配息、三大法人、個股法人 | 臺灣證券交易所（TWSE） |
| 正式 NAV、折溢價、成分股權重 | 國泰投信 |
| SOX、Nasdaq、TSM、NVDA、VIX、US10Y、USD/TWD | Yahoo Finance |

成交量與成交值以 **TWSE 為唯一準據**。實測 2026-09-24 官方為 10,442 張，
Yahoo 顯示 10,204 張 —— 混用來源會讓時間序列不可比。

### 不繞過機器人驗證

Stooq 與臺灣銀行牌告匯率在 2026-09 分別加上了 JavaScript proof-of-work 與
Imperva challenge。本專案**不繞過**這類機制，兩者皆已剔除並改用替代來源。
`sources/base.py` 會偵測驗證頁並把該欄位標為 `UNAVAILABLE`，而不是靜默失敗。

## 資料品質原則

每個欄位都帶 `{value, source, url, as_of, status}`，`status` 為
`ok` / `missing` / `stale` / `conflict` / `unavailable`。

**缺值絕不以推估值或鄰近日期代算。** 例如當日正式 NAV 尚未發布時，
折溢價就是 `missing`，不會拿前一日 NAV 頂替。

依覆蓋率分級發布：

| 可計分構面 | 行為 |
|---|---|
| ≥ 90 | 發布「確定」分數 |
| 70–89 | 發布「暫定」分數，回補後升級 |
| < 70 | 不計算 EOS，只顯示已取得的分項 |

這個門檻是實測得出的：覆蓋率 ≥96% 的三天與人工判讀誤差為 0 / −2 / +5，
覆蓋率 75–83% 的四天則擴大到 −12 ~ +16。

計分規則全部寫在 YAML，`eos/rubric.py` 只是解釋器 —— 調整門檻不需要改 Python。
8 個有人工判讀可對照的錨點日被鎖在 `tests/test_engine.py` 的回歸測試裡，
改動 rubric 就會失敗。資料齊全的兩天（9/3、9/24）誤差為 −2 與 **0**。

## 收集排程

| 窗 | 台灣時間 | 內容 |
|---|---|---|
| W1 | 交易日 15:10 | 00881 收盤、成分股、三大法人 |
| W2 | 交易日 19:30（21:30 重試） | 正式 NAV、成分股權重 |
| W3 | 次日 05:30 | 美股與風險指標 |
| W4 | 次日 09:00 | 回補過去 7 天所有 `missing` 欄位 |

台股 T 日一律使用美股 **T−1** 的時段，避免用到台股收盤後才發生的資訊
（lookahead bias）。

## 手機介面

前端是零相依的靜態 PWA（`index.html` / `app.css` / `app.js` / `sw.js`），
圖表為手寫 SVG —— 不依賴任何 CDN，這個專案已經被 Stooq 與台銀的機器人驗證教訓過一次。

啟用方式：GitHub repo → Settings → Pages → Source 選 **Deploy from a branch**，
branch `main`、資料夾 `/ (root)`。網址為 `https://<帳號>.github.io/eos-00881/`，
手機開啟後可「加入主畫面」，離線時仍可讀取已快取的歷史。

Service Worker 對外殼採 cache-first、對 `data/` 採 network-first ——
顯示過期的 EOS 又看不出來，比多載一秒糟糕得多。

本機預覽：

```bash
python -m http.server 8811
```

## 執行

```bash
python collect.py --window w1              # 台股收盤窗
python collect.py --window all --date 2026-09-24
python collect.py --window w4              # 回補過去 7 天的缺值
python -m scripts.show_snapshot 2026-09-24 # 檢視某日每個欄位的值與來源
```

每個部件（價格／法人／NAV／成分股／海外）獨立失敗，一個來源掛掉不會拖垮整天的收集；
所有部件皆為冪等，重跑同一個窗不會造成重複或損壞。

## 開發

```bash
pip install -r requirements.txt
pytest
```

測試完全以 `tests/fixtures/` 的真實 API 回應驅動，不連外網。
斷言刻意寫死已驗證的實際數值 —— 來源改版時應該立刻失敗，而不是
讓平台安靜地記錄錯誤資料。

每週排程的 `contracts` job 會對真實來源打一次，偵測來源腐化。

## 狀態

- [x] Phase 1 歷史回填（1,407 個交易日，2020-12-10 起，含息調整序列）
- [x] Phase 2 資料源 adapter 與測試
- [x] Phase 3a 計分核心（`eos/rubric.py`、`eos/engine.py`）
- [x] Phase 3b 收集協調器（`collect.py`，四個時間窗與回補）
- [x] Phase 4 手機前端（PWA）
- [ ] Phase 5 排程與 Email 通知
- [ ] Phase 6 機率模型（v2.0）

詳見 [`00881_platform_spec.md`](00881_platform_spec.md)。
