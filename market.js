/* 台股大盤 外資續買燈號 — 手機優先的靜態前端。

   版面元件與 00881 那頁共用（common.js）；這裡只放大盤自己的東西：
   七因子的名稱與權重、波段統計、外資買賣超長條圖、欄位說明。

   模型定義見 market_rubric_v1.0.yaml，逐項對齊原工作表的〈模型與定義〉。 */
(() => {
  "use strict";

  const INSTRUMENT = "TWMARKET";
  const HISTORY_URL = "data/eos_history_" + INSTRUMENT + ".json";
  const DAILY_URL = (d) => "data/daily/" + INSTRUMENT + "/" + d + ".json";

  const { $, el, fmt, pct, smart, table } = EOSUI;

  // 七因子。權重與 market_rubric_v1.0.yaml 一致 —— 改 YAML 時這裡要一起改，
  // 但分數本身來自快照，不是這裡算的，所以不會有「網頁算出另一個總分」的風險。
  const FACTORS = [
    ["F1", "本波連買天數餘裕", 15],
    ["F2", "本波累計規模餘裕", 15],
    ["F3", "外資期貨 5 日回補", 20],
    ["F4", "距前高壓力空間", 15],
    ["F5", "融資熱度（反向）", 10],
    ["F6", "前一美股費半", 15],
    ["F7", "美 10 年期公債殖利率", 10],
  ];

  const THRESHOLDS = [45, 65];
  // 三級評等借用 00881 色階的 1／3／5，兩頁的顏色語意才一致
  const RANK = { "賣壓風險升高": 1, "可續買但進入賣壓測試區": 3, "續買動能明確": 5 };
  const STATUS_LABEL = {
    confirmed: "資料完整", provisional: "暫定（部分缺值）", insufficient: "資料不足",
  };

  let history = [];
  let range = 30;

  /* ---------------------------------------------------------- 今日燈號 */
  function renderHero(row, prev) {
    $("#hero-eos").textContent = row.eos ?? "--";

    const chip = $("#hero-rating");
    chip.textContent = row.rating || (row.status === "insufficient" ? "未出分" : "—");
    chip.style.background = "var(--rank-" + (RANK[row.rating] || 3) + ")";
    chip.style.opacity = row.rating ? "1" : ".45";

    const cov = $("#hero-coverage");
    cov.textContent = (STATUS_LABEL[row.status] || row.status) + " " + fmt(row.coverage, 0) + "/100";
    cov.dataset.s = row.status || "";

    const d = $("#hero-delta");
    if (row.eos != null && prev && prev.eos != null) {
      const diff = row.eos - prev.eos;
      const sign = diff > 0 ? "▲" : diff < 0 ? "▼" : "－";
      d.textContent = sign + " " + Math.abs(diff) + "　較 " + prev.date.slice(5);
    } else {
      d.textContent = "";
    }

    const note = $("#hero-note");
    if (row.status === "insufficient") {
      note.textContent = "當日可計分因子僅 " + fmt(row.coverage, 0) +
        "/100，依規則不計算燈號。已取得的因子仍顯示於下方。";
    } else if (row.status === "provisional") {
      note.textContent = "部分欄位尚未取得（常見為期交所定版資料隔日才更新）。回補後燈號會自動更新。";
    } else {
      note.textContent = "";
    }
  }

  /* ---------------------------------------------------------- 明日展望 */
  /* 只在快照帶了 outlook 才顯示。回填的歷史沒有這個欄位（收集器加上去時
     那些日子早就收完了），此時整張卡不出現，而不是顯示一個空殼。 */
  function renderOutlook(snap, row) {
    const card = $("#outlook-card");
    const o = ((snap || {}).eos || {}).outlook;
    if (!o || o.eos == null) { card.hidden = true; return; }
    card.hidden = false;

    const box = $("#outlook");
    box.textContent = "";

    const line = el("div", "cross-row");
    const fig = el("div", "cross-figure");
    fig.textContent = o.eos;
    const side = el("div", "cross-side");

    const chip = el("span", "chip rating");
    chip.textContent = o.rating || "—";
    chip.style.background = "var(--rank-" + (RANK[o.rating] || 3) + ")";
    side.append(chip);

    const sub = el("div", "subtle");
    const diff = row && row.eos != null ? o.eos - row.eos : null;
    sub.textContent = (o.us_session ? "美股 " + o.us_session + " 時段" : "") +
      (diff == null ? "" : "　較當日 " + (diff > 0 ? "+" : "") + diff);
    side.append(sub);

    line.append(fig, side);
    box.append(line);

    if (o.same_as_today) {
      const n = el("p", "note");
      n.textContent = "與當日燈號相同：隔夜美股沒有帶來足以改變評分的變化。";
      box.append(n);
    }
  }

  /* ---------------------------------------------------------- 七因子 */
  /* 分數在快照的 eos.dimensions.LIGHT.items 裡，不在精簡歷史檔，
     所以要等當日快照載進來才畫。 */
  function renderFactors(snap) {
    const items = (((snap || {}).eos || {}).dimensions || {}).LIGHT;
    const byId = new Map(((items || {}).items || []).map((i) => [i.id, i]));
    EOSUI.barList($("#dims"), FACTORS.map(([key, name, max]) => {
      const it = byId.get(key);
      return { key, name, max, earned: it ? it.earned : null,
               avail: it && it.scored ? it.available : 0 };
    }));
  }

  /* ---------------------------------------------------------- 燈號走勢 */
  function renderChart(rows) {
    EOSUI.scoreChart($("#chart-wrap"), rows, {
      thresholds: THRESHOLDS,
      ariaLabel: "外資續買燈號走勢",
      tooltip: (r) =>
        r.date + "<br>燈號 <b>" + (r.eos ?? "—") + "</b>" +
        "（" + (r.rating || STATUS_LABEL[r.status] || "—") + "）<br>" +
        '<span class="subtle">加權 ' + price(r.taiex) +
        "　外資 " + signed100m(r.market_foreign_net_100m) + "</span>",
      onPick: (r) => { $("#day-picker").value = r.date; loadDetail(r.date); },
    });
  }

  /* 金額一律走這一個函式。先前「壓力點」卡片用 toFixed(1) 而明細表用
     toLocaleString，同一筆 -329.65 在同一頁上出現 -329.6 與 -329.7 兩種寫法 ——
     看起來像兩個不同的數字。四捨五入只做一次，格式只有一份。 */
  function money100m(v, withSign) {
    if (v === null || v === undefined) return "—";
    const r = Math.round(v * 10) / 10;
    const body = Math.abs(r).toLocaleString("en-US", { minimumFractionDigits: 1,
                                                       maximumFractionDigits: 1 });
    const sign = r < 0 ? "-" : (withSign ? "+" : "");
    return sign + body + " 億";
  }
  const signed100m = (v) => money100m(v, true);

  function lots(v, withSign) {
    if (v === null || v === undefined) return "—";
    const sign = v < 0 ? "-" : (withSign ? "+" : "");
    return sign + Math.abs(v).toLocaleString("en-US") + " 口";
  }

  const price = (v) => (v === null || v === undefined ? "—"
    : v.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }));

  // 漲跌類的百分比一定要看得到正負號；距前高這種「距離」則不需要
  const spct = (v, d = 2) =>
    v === null || v === undefined ? "—" : (v >= 0 ? "+" : "") + (v * 100).toFixed(d) + "%";

  /* ---------------------------------------------------------- 外資買賣超 */
  /* 圍繞 0 的雙向長條。用長條而不是折線：買賣超是「一天一筆的量」，
     折線會暗示日與日之間有連續的中間值。 */
  function renderFlow(rows) {
    const wrap = $("#flow-chart");
    wrap.textContent = "";
    const pts = rows.filter((r) => r.market_foreign_net_100m != null);
    if (!pts.length) { wrap.textContent = "尚無資料"; return; }

    const NS = "http://www.w3.org/2000/svg";
    const W = 340, H = 150, P = { t: 12, r: 34, b: 20, l: 8 };
    const iw = W - P.l - P.r, ih = H - P.t - P.b;

    const vals = pts.map((r) => r.market_foreign_net_100m);
    const span = Math.max(Math.abs(Math.min(...vals)), Math.abs(Math.max(...vals))) || 1;
    const Y = (v) => P.t + ih / 2 - (v / span) * (ih / 2);
    const step = iw / pts.length;
    const bw = Math.max(1.2, Math.min(9, step * 0.68));

    const svg = document.createElementNS(NS, "svg");
    svg.setAttribute("viewBox", "0 0 " + W + " " + H);
    svg.setAttribute("role", "img");
    svg.setAttribute("aria-label", "外資現貨買賣超，共 " + pts.length + " 個交易日");

    const add = (tag, attrs, text) => {
      const n = document.createElementNS(NS, tag);
      for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
      if (text != null) n.textContent = text;
      svg.append(n); return n;
    };

    // 上下各一條刻度 + 零軸。刻度取整百，讀起來才像金額而不是像素
    const tick = Math.max(100, Math.round(span / 200) * 100);
    for (const v of [tick, -tick]) {
      if (Math.abs(v) > span) continue;
      add("line", { x1: P.l, x2: P.l + iw, y1: Y(v), y2: Y(v),
                    stroke: "var(--grid)", "stroke-width": 1 });
      add("text", { x: P.l + iw + 4, y: Y(v) + 3, "font-size": 8,
                    fill: "var(--muted)" }, (v > 0 ? "+" : "") + v);
    }
    add("line", { x1: P.l, x2: P.l + iw, y1: Y(0), y2: Y(0),
                  stroke: "var(--axis)", "stroke-width": 1 });
    add("text", { x: P.l + iw + 4, y: Y(0) + 3, "font-size": 8,
                  fill: "var(--muted)" }, "0");

    pts.forEach((r, i) => {
      const v = r.market_foreign_net_100m;
      const x = P.l + step * (i + 0.5) - bw / 2;
      const y = v >= 0 ? Y(v) : Y(0);
      // 單一顏色，方向由零軸上下決定。不用紅綠：台股紅漲綠跌，
      // 而全站的評級色階刻意避開紅綠，兩套語意混在同一頁會互相干擾。
      svg.append(EOSUI.rect(x, y, bw, Math.max(0.8, Math.abs(Y(v) - Y(0))),
                            "var(--series-1)", 1));
    });

    [0, pts.length - 1].filter((v, i, a) => a.indexOf(v) === i).forEach((i) => {
      add("text", { x: P.l + step * (i + 0.5), y: H - 5, "font-size": 8,
                    fill: "var(--muted)",
                    "text-anchor": i === 0 ? "start" : "end" }, pts[i].date.slice(5));
    });

    wrap.append(svg);
  }

  /* ---------------------------------------------------------- 波段統計 */
  const WAVE_DIR = { buy: "買超", sell: "賣超" };

  function renderWaves(snap) {
    const f = (snap || {}).fields || {};
    const v = (n) => (f[n] || {}).value;
    const box = $("#wave");
    box.textContent = "";

    const days = v("window_days");
    $("#wave-window").textContent = days ? "視窗 " + days + " 個交易日" : "";

    const money = signed100m;
    const dnum = (x) => (x == null ? "—" : x.toFixed(1) + " 天");

    const head = ["", "本波", "買波", "賣波"];
    const rows = [
      ["方向", WAVE_DIR[v("wave_direction")] || "—", "—", "—"],
      ["天數", v("wave_days") == null ? "—" : v("wave_days") + " 天",
       dnum(v("avg_buy_wave_days")), dnum(v("avg_sell_wave_days"))],
      ["累計", money(v("wave_cumulative_100m")),
       money(v("avg_buy_wave_cumulative_100m")), money(v("avg_sell_wave_cumulative_100m"))],
      ["視窗內段數", "—",
       v("completed_buy_waves") == null ? "—" : v("completed_buy_waves") + " 段",
       v("completed_sell_waves") == null ? "—" : v("completed_sell_waves") + " 段"],
      ["視窗內最長買波", "—",
       v("max_buy_wave_days") == null ? "—" : v("max_buy_wave_days") + " 天", "—"],
      ["視窗內最大買波", "—", money(v("max_buy_wave_cumulative_100m")), "—"],
    ];
    box.append(table(head, rows));

    const n = el("p", "legend-note subtle");
    n.textContent = "「買波／賣波」兩欄為視窗內已完成波段的平均值。" +
      "F1 是本波天數相對最長買波的餘裕，F2 是本波累計相對最大買波的餘裕 —— " +
      "餘裕越大分數越高，代表這一波還沒走完。" +
      (v("wave_start") ? "本波起於 " + v("wave_start") + "。" : "");
    box.append(n);
  }

  /* ---------------------------------------------------------- 壓力與支撐 */
  /* 點位由 eos/marketflow.py 算好存進快照（eos.levels），由高到低排序。
     這裡把「最新收盤」插進它該在的位置 —— 一張由高到低的表加上一條
     標示現價的線，比「壓力區／支撐區」兩個分開的清單更快看懂距離。 */
  function renderLevels(snap) {
    const card = $("#levels-card");
    const L = ((snap || {}).eos || {}).levels;
    if (!L || !(L.rows || []).length) { card.hidden = true; return; }
    card.hidden = false;

    $("#levels-window").textContent = L.window_days
      ? "視窗 " + L.window_days + " 個交易日" : "";

    const close = L.latest_close;
    const box = $("#levels");
    box.textContent = "";

    const rows = [];
    let placed = false;
    for (const r of L.rows) {
      if (!placed && close != null && r.value < close) {
        rows.push(closeRow(close));
        placed = true;
      }
      rows.push(levelRow(r, close));
    }
    if (!placed && close != null) rows.push(closeRow(close));

    box.append(table(["點位", "指數", "距現價"], rows));
  }

  function levelRow(r, close) {
    const left = el("div");
    const t = el("div", "fld-label");
    t.textContent = r.label;
    const sub = el("div", "fld-sub subtle");
    sub.textContent = (r.date ? r.date + "　" : "") + r.basis;
    left.append(t, sub);
    return [
      { node: left },
      { node: textNode(price(r.value)), cls: "num" },
      { node: textNode(r.gap_pct == null ? "—" : spct(r.gap_pct)), cls: "num" },
    ];
  }

  function closeRow(close) {
    const left = el("div");
    const t = el("div", "fld-label");
    t.textContent = "▶ 最新收盤";
    left.append(t);
    return [
      { node: left, cls: "now" },
      { node: textNode(price(close)), cls: "num now" },
      { node: textNode("—"), cls: "num now" },
    ];
  }

  /* ---------------------------------------------------------- 資金指標 */
  function renderKeys(snap) {
    const f = (snap || {}).fields || {};
    const v = (n) => (f[n] || {}).value;
    const box = $("#keys");
    box.textContent = "";

    const gap = v("gap_to_resistance_pct");
    const rows = [
      // F4 的分母是盤中高，不是最高收盤 —— 這裡要顯示同一個數字，
      // 否則「前高」與「距前高」在同一張表上會對不起來
      ["前高（期間最高盤中價）",
       v("resistance_high") == null ? "—" : price(v("resistance_high")) +
         (v("resistance_high_date") ? "　" + v("resistance_high_date") : "")],
      ["目前距前高　→ F4", gap == null ? "—" : pct(gap, 3)],
      ["加權指數", v("taiex") == null ? "—" : price(v("taiex")) +
        (v("taiex_change_pct") == null ? "" : "　" + spct(v("taiex_change_pct")))],
      ["成交值", money100m(v("market_turnover_100m"))],
      ["外資現貨買賣超", signed100m(v("market_foreign_net_100m"))],
      ["三大法人合計", signed100m(v("institutional_net_100m"))],
      ["外資期貨淨未平倉", lots(v("foreign_futures_net_oi"))],
      ["外資期貨 5 日變化", lots(v("foreign_futures_oi_change_5d"), true)],
      ["融資餘額", money100m(v("margin_balance_100m"))],
      ["融資 2 日變化", signed100m(v("margin_change_2d_100m"))],
      ["費半（對應時段）", spct(v("sox_prev_session_ret"))],
      ["美 10 年期公債殖利率", v("us10y") == null ? "—" : fmt(v("us10y"), 3) + "%"],
    ];
    box.append(table(["項目", "數值"], rows));
  }

  /* ---------------------------------------------------------- 當日明細 */
  // 帶正負號的是「變化量」與「淨額」；餘額、成交值這類水準值不加號
  const SIGNED_PCT = new Set(["taiex_change_pct", "sox_prev_session_ret",
                              "sox_latest_session_ret"]);
  const PLAIN_PCT = new Set(["gap_to_resistance_pct"]);
  const M100_SIGNED = new Set([
    "foreign_net_100m", "institutional_net_100m", "market_foreign_net_100m",
    "margin_change_100m", "margin_change_2d_100m", "wave_cumulative_100m",
    "avg_buy_wave_cumulative_100m", "max_buy_wave_cumulative_100m",
    "avg_sell_wave_cumulative_100m",
  ]);
  const M100_PLAIN = new Set(["market_turnover_100m", "margin_balance_100m",
                              "margin_prev_100m"]);
  const OI_SIGNED = new Set(["foreign_futures_net_oi", "dealer_futures_net_oi",
                             "trust_futures_net_oi", "foreign_futures_oi_change_5d"]);
  const OI_PLAIN = new Set(["foreign_futures_long_oi", "foreign_futures_short_oi"]);
  const PRICE_FIELDS = new Set(["taiex", "taiex_change", "resistance_close",
                                "latest_close"]);
  // 沒有單位的裸數字讀起來像代碼。天數、段數、日數都補上量詞。
  const UNIT = {
    wave_days: "天", max_buy_wave_days: "天", avg_buy_wave_days: "天",
    avg_sell_wave_days: "天",
    completed_buy_waves: "段", completed_sell_waves: "段",
    window_days: "個交易日",
  };

  // g = 分組，o = 組內順序，label = 中文說明，f = 對應的模型因子
  const FIELD_META = {
    taiex: { g: "大盤行情", o: 1, label: "加權指數收盤" },
    taiex_change: { g: "大盤行情", o: 2, label: "漲跌點數" },
    taiex_change_pct: { g: "大盤行情", o: 3, label: "漲跌幅" },
    market_turnover_100m: { g: "大盤行情", o: 4, label: "成交值" },

    market_foreign_net_100m: { g: "法人資金", o: 1, label: "外資現貨買賣超", f: "F1/F2" },
    foreign_net_100m: { g: "法人資金", o: 2, label: "外資買賣超（同上，供 00881 模型使用）" },
    institutional_net_100m: { g: "法人資金", o: 3, label: "三大法人合計" },

    foreign_futures_net_oi: { g: "期貨未平倉", o: 1, label: "外資淨未平倉", f: "F3" },
    foreign_futures_oi_change_5d: { g: "期貨未平倉", o: 2, label: "外資 5 日變化", f: "F3" },
    foreign_futures_long_oi: { g: "期貨未平倉", o: 3, label: "外資多方未平倉" },
    foreign_futures_short_oi: { g: "期貨未平倉", o: 4, label: "外資空方未平倉" },
    dealer_futures_net_oi: { g: "期貨未平倉", o: 5, label: "自營商淨未平倉" },
    trust_futures_net_oi: { g: "期貨未平倉", o: 6, label: "投信淨未平倉" },

    margin_balance_100m: { g: "融資", o: 1, label: "融資餘額" },
    margin_prev_100m: { g: "融資", o: 2, label: "前一日融資餘額" },
    margin_change_100m: { g: "融資", o: 3, label: "融資單日變化" },
    margin_change_2d_100m: { g: "融資", o: 4, label: "融資 2 日變化", f: "F5" },

    wave_direction: { g: "波段", o: 1, label: "本波方向" },
    wave_start: { g: "波段", o: 2, label: "本波起始日" },
    wave_days: { g: "波段", o: 3, label: "本波天數", f: "F1" },
    wave_cumulative_100m: { g: "波段", o: 4, label: "本波累計", f: "F2" },
    completed_buy_waves: { g: "波段", o: 5, label: "視窗內已完成買波數" },
    avg_buy_wave_days: { g: "波段", o: 6, label: "買波平均天數" },
    max_buy_wave_days: { g: "波段", o: 7, label: "買波最長天數", f: "F1 分母" },
    avg_buy_wave_cumulative_100m: { g: "波段", o: 8, label: "買波平均累計" },
    max_buy_wave_cumulative_100m: { g: "波段", o: 9, label: "買波最大累計", f: "F2 分母" },
    completed_sell_waves: { g: "波段", o: 10, label: "視窗內已完成賣波數" },
    avg_sell_wave_days: { g: "波段", o: 11, label: "賣波平均天數" },
    avg_sell_wave_cumulative_100m: { g: "波段", o: 12, label: "賣波平均累計" },

    resistance_close: { g: "壓力與視窗", o: 1, label: "視窗內最高收盤" },
    resistance_date: { g: "壓力與視窗", o: 2, label: "前高日期" },
    latest_close: { g: "壓力與視窗", o: 3, label: "最新收盤" },
    gap_to_resistance_pct: { g: "壓力與視窗", o: 4, label: "距前高", f: "F4" },
    window_days: { g: "壓力與視窗", o: 5, label: "視窗交易日數" },

    sox_prev_session_ret: { g: "海外", o: 1, label: "費半（T−1 時段）", f: "F6" },
    us10y: { g: "海外", o: 2, label: "美 10 年期公債殖利率", f: "F7" },
    sox_latest_session_ret: { g: "海外", o: 3, label: "費半（最新時段）", f: "明日展望" },
    us10y_latest: { g: "海外", o: 4, label: "美 10 年期（最新）", f: "明日展望" },
  };

  const GROUP_ORDER = ["大盤行情", "法人資金", "期貨未平倉", "融資",
                       "波段", "壓力與視窗", "海外", "其他"];

  const STATUS_MARK = {
    ok: "", stale: "較舊時點", missing: "缺值",
    conflict: "來源不一致", unavailable: "來源不可得",
  };

  function display(name, value) {
    if (value === null || value === undefined) return "—";
    if (SIGNED_PCT.has(name)) return spct(value, 3);
    if (PLAIN_PCT.has(name)) return pct(value, 3);
    if (M100_SIGNED.has(name)) return money100m(value, true);
    if (M100_PLAIN.has(name)) return money100m(value);
    if (OI_SIGNED.has(name)) return lots(value, true);
    if (OI_PLAIN.has(name)) return lots(value);
    if (PRICE_FIELDS.has(name)) {
      return (name === "taiex_change" && value >= 0 ? "+" : "") + price(value);
    }
    if (name === "us10y" || name === "us10y_latest") return fmt(value, 3) + "%";
    if (name === "wave_direction") return WAVE_DIR[value] || String(value);
    if (UNIT[name]) {
      const n = Number.isInteger(value) ? String(value) : value.toFixed(1);
      return n + " " + UNIT[name];
    }
    return smart(value);
  }

  async function loadDetail(day) {
    const box = $("#detail");
    let snap;
    try {
      snap = await EOSUI.loadJSON(DAILY_URL(day));
    } catch {
      box.textContent = "該日快照尚未取得。";
      return;
    }

    const row = history.find((r) => r.date === day);
    renderFactors(snap);
    renderOutlook(snap, row);
    // 結論隨選取的日期走：翻歷史時看到的是那一天自己的結論
    EOSUI.renderSummary($("#summary-card"), $("#summary"),
                        ((snap || {}).eos || {}).summary);
    renderWaves(snap);
    renderLevels(snap);
    renderKeys(snap);

    const fields = snap.fields || {};
    const groups = new Map();
    for (const name of Object.keys(fields)) {
      const meta = FIELD_META[name] || { g: "其他", label: name };
      if (!groups.has(meta.g)) groups.set(meta.g, []);
      groups.get(meta.g).push([name, meta]);
    }

    box.textContent = "";
    for (const g of GROUP_ORDER) {
      const items = groups.get(g);
      if (!items || !items.length) continue;

      const h = el("h3", "grp");
      h.textContent = g;
      box.append(h);

      const rows = items
        .sort((a, b) => (a[1].o ?? 999) - (b[1].o ?? 999))
        .map(([name, meta]) => {
          const f = fields[name] || {};
          const left = el("div");
          const t = el("div", "fld-label");
          t.textContent = meta.label;
          const sub = el("div", "fld-sub subtle");
          sub.textContent = name + (meta.f ? "　→ " + meta.f : "") +
                            (f.source ? "　" + f.source : "");
          left.append(t, sub);

          const right = el("div");
          const val = el("div", "fld-val");
          val.textContent = display(name, f.value);
          right.append(val);
          const mark = STATUS_MARK[f.status];
          if (mark) {
            const m = el("div", "fld-sub subtle");
            m.textContent = mark + (f.as_of ? "　" + f.as_of : "");
            right.append(m);
          }
          return [{ node: left }, { node: right, cls: "num" }];
        });
      box.append(table(["項目", "數值"], rows));
    }
  }

  /* ---------------------------------------------------------- 連續買賣 Top5 */
  /* 報表由 eos/stockflow.py 在收集時算好（data/top5_streaks.json），
     這裡只負責挑法人別、濾 ETF、排版。連續天數不在前端算：
     那需要載入整個視窗的逐檔明細，手機上是幾 MB 的下載量。 */
  let streaksData = null;
  let institution = "foreign";
  let hideEtf = false;

  function renderStreaks() {
    const card = $("#streaks-card");
    const block = ((streaksData || {}).institutions || {})[institution];
    if (!block) { card.hidden = true; return; }
    card.hidden = false;

    const n = streaksData.top_n || 5;
    const box = $("#streaks");
    box.textContent = "";

    for (const [side, title] of [["buy", "連續買超"], ["sell", "連續賣超"]]) {
      const all = block[side] || [];
      const rows = all.filter((r) => !(hideEtf && r.etf)).slice(0, n);

      const h = el("h3", "grp");
      h.textContent = title;
      box.append(h);

      if (!rows.length) {
        const p = el("p", "note");
        // 兩種空的原因完全不同，講錯會讓人以為市場上沒有連續買賣超
        p.textContent = all.length
          ? "達到門檻的" + title.slice(2) + "標的全是 ETF，已被「排除 ETF」濾掉。"
          : "視窗內沒有達到 " + (streaksData.min_days || 3) +
            " 日門檻的" + title.slice(2) + "標的。";
        box.append(p);
        continue;
      }

      box.append(table(["代號／名稱", "天數", "累計", "起始"], rows.map((r) => {
        const left = el("div");
        const t = el("div", "fld-label");
        t.textContent = r.name || r.code;
        const sub = el("div", "fld-sub subtle");
        sub.textContent = r.code + (r.etf ? "　ETF" : "");
        left.append(t, sub);
        return [
          { node: left },
          // ≥ 表示連到視窗最舊一天，真正天數可能更長，不能當成確定值
          { node: textNode((r.truncated ? "≥" : "") + r.days + " 天"), cls: "num" },
          { node: textNode(lotsOf(r)), cls: "num" },
          { node: textNode(r.start.slice(5)), cls: "num" },
        ];
      })));
    }
  }

  const textNode = (s) => { const d = el("div"); d.textContent = s; return d; };

  const lotsOf = (r) => {
    const v = r.lots;
    if (v === null || v === undefined) return "—";
    const sign = v < 0 ? "-" : "+";
    return sign + Math.abs(Math.round(v)).toLocaleString("en-US") + " 張";
  };

  function initStreakControls() {
    document.querySelectorAll("[data-inst]").forEach((b) => {
      b.addEventListener("click", () => {
        document.querySelectorAll("[data-inst]").forEach((x) => x.classList.remove("is-on"));
        b.classList.add("is-on");
        institution = b.dataset.inst;
        renderStreaks();
      });
    });
    const cb = $("#streaks-no-etf");
    if (cb) {
      cb.addEventListener("change", () => { hideEtf = cb.checked; renderStreaks(); });
    }
  }

  async function loadStreaks() {
    try {
      streaksData = await EOSUI.loadJSON("data/top5_streaks.json");
    } catch {
      $("#streaks-card").hidden = true;     // 還沒回填逐檔資料時整張不顯示
      return;
    }
    const bits = [streaksData.as_of];
    if (streaksData.window_start) {
      bits.push("視窗 " + streaksData.window_start + " 起 " +
                streaksData.window_days + " 個交易日");
    }
    $("#streaks-asof").textContent = bits.join("　");
    renderStreaks();
  }

  /* ---------------------------------------------------------- 歷史表格 */
  function renderHistTable(rows) {
    const box = $("#hist-table");
    box.textContent = "";
    const body = [...rows].reverse().map((r) => [
      r.date,
      r.eos ?? "—",
      r.rating || STATUS_LABEL[r.status] || "—",
      r.taiex == null ? "—" : price(r.taiex),
      signed100m(r.market_foreign_net_100m),
      lots(r.foreign_futures_net_oi, true),
    ]);
    box.append(table(["日期", "燈號", "評級", "加權", "外資", "期貨淨OI"], body));
  }

  /* ---------------------------------------------------------- 組裝 */
  function slice() {
    return range === "all" ? history : history.slice(-Number(range));
  }

  function render() {
    const rows = slice();
    const latest = history[history.length - 1];
    const prev = history.filter((r) => r.eos != null).slice(-2)[0];
    if (latest) renderHero(latest, prev && prev.date !== latest.date ? prev : null);
    renderChart(rows);
    renderFlow(rows);
    renderHistTable(rows);
    EOSUI.syncRangeButtons(history.length);
    const cap = $("#chart-count");
    if (cap) {
      cap.textContent = rows.length === history.length
        ? "顯示全部 " + rows.length + " 個交易日"
        : "顯示最近 " + rows.length + " 個交易日（累積 " + history.length + "）";
    }
  }

  async function main() {
    EOSUI.initTheme(render);
    try {
      history = await EOSUI.loadJSON(HISTORY_URL);
    } catch {
      $("#fallback").textContent = "無法載入大盤資料，且無離線快取可用。";
      return;
    }
    history = (history || []).filter((r) => r && r.date)
      .sort((a, b) => a.date.localeCompare(b.date));
    if (!history.length) { $("#fallback").textContent = "尚無任何每日快照。"; return; }

    const picker = $("#day-picker");
    [...history].reverse().forEach((r) => {
      const o = el("option"); o.value = r.date; o.textContent = r.date; picker.append(o);
    });
    picker.addEventListener("change", () => loadDetail(picker.value));

    EOSUI.initRangeControls((r) => { range = r; render(); });
    EOSUI.initTableToggles();
    initStreakControls();
    render();

    const latest = history[history.length - 1];
    $("#asof").textContent = "最後更新交易日 " + latest.date +
                             "　共 " + history.length + " 個交易日";
    await loadDetail(latest.date);

    $("#fallback").hidden = true;
    $("#app").hidden = false;

    // 不 await：逐檔報表比較大，抓不到也不該卡住主要內容
    loadStreaks();

    EOSUI.crossSummary($("#cross-card"), $("#cross-body"), "00881", {
      rankOf: (r) => ({ "高風險區": 1, "偏不利": 2, "中性等待": 3,
                        "偏有利": 4, "高機會區": 5 })[r] || 3,
      note: (r) => "00881 的 Entry Opportunity Score 由折溢價、含息趨勢、成分股廣度、" +
                   "海外科技、風險環境與量價六個構面組成，" + r.date +
                   " 覆蓋率 " + fmt(r.coverage, 0) + "/100。",
    });
  }

  EOSUI.registerSW();
  main();
})();
