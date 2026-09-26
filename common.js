/* 00881 / 台股大盤 共用前端元件。

   兩頁的差別只有「分數叫什麼、哪些門檻、每列顯示什麼」，
   骨架（主題切換、走勢圖、分項長條、結論區塊、表格）完全一樣。
   共用一份而不是複製一份：圖表改一次兩頁都會改到，
   不會出現「大盤那頁的圖還停在舊版樣式」這種事。

   一樣不引入任何外部函式庫 —— SVG 全部手寫。 */
window.EOSUI = (() => {
  "use strict";

  const NS = "http://www.w3.org/2000/svg";

  const $ = (sel, root) => (root || document).querySelector(sel);
  const el = (tag, cls) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    return n;
  };

  const fmt = (v, d = 2) =>
    v === null || v === undefined ? "—" :
    typeof v === "boolean" ? (v ? "是" : "否") :
    typeof v === "number" ? (Number.isInteger(v) ? String(v) : v.toFixed(d)) : String(v);

  const pct = (v, d = 2) => (v === null || v === undefined ? "—" : (v * 100).toFixed(d) + "%");

  const signed = (v, d = 2) =>
    v === null || v === undefined ? "—" : (v >= 0 ? "+" : "") + v.toFixed(d);

  /* 依量級決定小數位。成交量寫成 18245.2160 只是雜訊，
     而 RSI 或折溢價需要看到小數 —— 固定位數兩邊都討好不了。 */
  function smart(v) {
    if (v === null || v === undefined) return "—";
    if (typeof v === "boolean") return v ? "是" : "否";
    // 防呆：結構化欄位不該被當成單一數值輸出，否則會變成 [object Object]
    if (Array.isArray(v)) return v.length + " 筆明細";
    if (typeof v === "object") return "結構化資料";
    if (typeof v !== "number") return String(v);
    const a = Math.abs(v);
    if (Number.isInteger(v)) return v.toLocaleString("en-US");
    if (a >= 1000) return v.toLocaleString("en-US", { maximumFractionDigits: 0 });
    if (a >= 100) return v.toFixed(1);
    if (a >= 1) return v.toFixed(2);
    return v.toFixed(4);
  }

  function rect(x, y, w, h, fill, r) {
    const n = document.createElementNS(NS, "rect");
    n.setAttribute("x", x); n.setAttribute("y", y);
    n.setAttribute("width", Math.max(0, w)); n.setAttribute("height", h);
    n.setAttribute("fill", fill);
    if (r) { n.setAttribute("rx", r); n.setAttribute("ry", r); }
    return n;
  }

  /* ---------------------------------------------------------- 主題切換 */
  function initTheme(onChange) {
    let saved = null;
    try { saved = localStorage.getItem("theme"); } catch { /* 私密瀏覽會丟例外 */ }
    if (saved === "dark" || saved === "light") document.documentElement.dataset.theme = saved;
    const btn = $("#theme-toggle");
    if (!btn) return;
    btn.addEventListener("click", () => {
      const cur = document.documentElement.dataset.theme;
      const isDark = cur ? cur === "dark"
        : matchMedia("(prefers-color-scheme: dark)").matches;
      const next = isDark ? "light" : "dark";
      document.documentElement.dataset.theme = next;
      try { localStorage.setItem("theme", next); } catch { /* 忽略：僅影響偏好記憶 */ }
      // SVG 的顏色是畫上去的具體值，不像 CSS 會自己跟著變數更新，必須重畫
      if (onChange) onChange();
    });
  }

  /* ---------------------------------------------------------- 表格 */
  function table(head, rows) {
    const t = el("table");
    const thead = el("thead"), tr = el("tr");
    head.forEach((h) => { const th = el("th"); th.textContent = h; tr.append(th); });
    thead.append(tr); t.append(thead);
    const tb = el("tbody");
    rows.forEach((cells) => {
      const r = el("tr");
      cells.forEach((c) => {
        const td = el("td");
        if (c && typeof c === "object" && c.node) { td.append(c.node); if (c.cls) td.className = c.cls; }
        else { td.textContent = c ?? "—"; }
        r.append(td);
      });
      tb.append(r);
    });
    t.append(tb); return t;
  }

  /* ---------------------------------------------------------- 分項長條 */
  /* items: [{ key, name, earned, avail, max }]
     實心＝已得分，淡色軌道＝滿分，短豎線＝當日實際可計分上限。 */
  function barList(wrap, items) {
    wrap.textContent = "";
    const W = 100, H = 10;                    // 以 viewBox 百分比座標作圖

    for (const it of items) {
      const { key, name, earned, avail, max } = it;

      const line = el("div", "dim-row");
      const n = el("div", "dim-name");
      n.textContent = key ? key + "　" + name : name;
      const v = el("div", "dim-val");
      // 工作表的「滿分／今日／得分率」三欄，在窄畫面壓成一行
      v.textContent = earned == null
        ? "—／" + max
        : earned.toFixed(1) + "／" + max + "　" + Math.round((earned / max) * 100) + "%";
      line.append(n, v);

      const barBox = el("div", "dim-bar");
      const svg = document.createElementNS(NS, "svg");
      svg.setAttribute("viewBox", "0 0 " + W + " " + H);
      svg.setAttribute("preserveAspectRatio", "none");
      svg.setAttribute("height", "10");
      svg.setAttribute("role", "img");
      svg.setAttribute("aria-label",
        name + " " + (earned == null ? "缺值" : earned.toFixed(1)) + " 分，滿分 " + max + " 分");

      svg.append(rect(0, 0, W, H, "var(--track)", 3));
      if (earned != null && earned > 0) {
        svg.append(rect(0, 0, Math.max(1.2, (earned / max) * W), H, "var(--series-1)", 3));
      }
      if (avail != null && avail < max) {
        const x = (avail / max) * W;
        svg.append(rect(x - 0.35, -1, 0.7, H + 2, "var(--muted)", 0));
      }
      barBox.append(svg);
      line.append(barBox);
      wrap.append(line);
    }
  }

  /* ---------------------------------------------------------- 走勢圖 */
  /* 分數走勢。兩頁唯一的差別是門檻、標題與 tooltip 內容，都由 opt 帶入。 */
  function scoreChart(wrap, rows, opt) {
    const o = Object.assign({
      thresholds: [],
      ariaLabel: "分數走勢",
      valueOf: (r) => r.eos,
      isConfirmed: (r) => r.status === "confirmed",
      tooltip: (r) => r.date,
      onPick: null,
    }, opt || {});

    wrap.textContent = "";
    if (!rows.length) { wrap.textContent = "尚無資料"; return; }

    const W = 340, H = 190, P = { t: 10, r: 30, b: 22, l: 26 };
    const iw = W - P.l - P.r, ih = H - P.t - P.b;
    const svg = document.createElementNS(NS, "svg");
    svg.setAttribute("viewBox", "0 0 " + W + " " + H);
    svg.setAttribute("role", "img");
    svg.setAttribute("aria-label", o.ariaLabel + "，共 " + rows.length + " 個交易日");

    // 交易日以序位等距排列，避免週末假日在時間軸上留下空洞
    const X = (i) => P.l + (rows.length === 1 ? iw / 2 : (i / (rows.length - 1)) * iw);
    const Y = (v) => P.t + (1 - v / 100) * ih;

    const add = (tag, attrs, text) => {
      const n = document.createElementNS(NS, tag);
      for (const [k, val] of Object.entries(attrs)) n.setAttribute(k, val);
      if (text != null) n.textContent = text;
      svg.append(n); return n;
    };

    // 評級門檻：實線細線 + 右側標籤（虛線會被誤讀成投影或雜訊）
    for (const t of o.thresholds) {
      add("line", { x1: P.l, x2: P.l + iw, y1: Y(t), y2: Y(t),
                    stroke: "var(--grid)", "stroke-width": 1 });
      add("text", { x: P.l + iw + 4, y: Y(t) + 3, "font-size": 8,
                    fill: "var(--muted)" }, String(t));
    }
    add("line", { x1: P.l, x2: P.l + iw, y1: Y(0), y2: Y(0),
                  stroke: "var(--axis)", "stroke-width": 1 });
    // 標出 0，讓最下面那條線讀起來是刻度的一部分而不是游離的橫線。
    // y 軸固定 0–100 不做截斷：分數是有界的，截斷會放大視覺上的起伏。
    add("text", { x: P.l + iw + 4, y: Y(0) + 3, "font-size": 8,
                  fill: "var(--muted)" }, "0");

    // 線段：遇到未出分的日子斷開，不跨越缺口連線
    let seg = [];
    const flush = () => {
      if (seg.length > 1) {
        add("path", { d: seg.map((p, i) => (i ? "L" : "M") + p[0] + " " + p[1]).join(" "),
                      fill: "none", stroke: "var(--series-1)", "stroke-width": 2,
                      "stroke-linejoin": "round", "stroke-linecap": "round" });
      } else if (seg.length === 1) {
        add("circle", { cx: seg[0][0], cy: seg[0][1], r: 2, fill: "var(--series-1)" });
      }
      seg = [];
    };
    rows.forEach((r, i) => {
      const v = o.valueOf(r);
      if (v == null) { flush(); return; }
      seg.push([X(i), Y(v)]);
    });
    flush();

    // 標記點：實心＝確定，空心＝暫定（形狀作為色彩以外的第二編碼）
    rows.forEach((r, i) => {
      const v = o.valueOf(r);
      if (v == null) return;
      const solid = o.isConfirmed(r);
      add("circle", {
        cx: X(i), cy: Y(v), r: 3.2,
        fill: solid ? "var(--series-1)" : "var(--surface-1)",
        stroke: "var(--series-1)", "stroke-width": solid ? 0 : 2,
      });
    });

    // 端點直接標值（只標端點，不是每個點都掛數字）
    let lastIdx = -1;
    rows.forEach((r, i) => { if (o.valueOf(r) != null) lastIdx = i; });
    if (lastIdx >= 0) {
      const atEdge = lastIdx > rows.length - 3;
      // 靠右邊時往左推 9px：不然端點值會和右側的門檻刻度擠在一起，
      // 讀起來像「58 65」兩個數字連在一起。
      add("text", { x: atEdge ? X(lastIdx) - 9 : X(lastIdx),
                    y: Y(o.valueOf(rows[lastIdx])) - 9,
                    "font-size": 10, "font-weight": 600,
                    "text-anchor": atEdge ? "end" : "middle",
                    fill: "var(--ink)" }, String(o.valueOf(rows[lastIdx])));
    }

    // x 軸：首、中、尾三個刻度即可
    [0, Math.floor((rows.length - 1) / 2), rows.length - 1]
      .filter((v, i, a) => a.indexOf(v) === i)
      .forEach((i) => {
        add("text", { x: X(i), y: H - 6, "font-size": 8, fill: "var(--muted)",
                      "text-anchor": i === 0 ? "start" : i === rows.length - 1 ? "end" : "middle" },
            rows[i].date.slice(5));
      });

    const cross = add("line", { x1: 0, x2: 0, y1: P.t, y2: P.t + ih,
                                stroke: "var(--axis)", "stroke-width": 1, opacity: 0 });
    wrap.append(svg);

    const tip = el("div", "tip"); tip.hidden = true; wrap.append(tip);
    attachHover(wrap, svg, tip, cross, rows, X, W, P, iw, o);
  }

  /* 指標／觸控共用的最近點命中層。手指的命中區必須遠大於 3px 的點。 */
  function attachHover(wrap, svg, tip, cross, rows, X, W, P, iw, o) {
    const pick = (clientX) => {
      const box = svg.getBoundingClientRect();
      const vx = ((clientX - box.left) / box.width) * W;
      const t = rows.length === 1 ? 0 : ((vx - P.l) / iw) * (rows.length - 1);
      return Math.max(0, Math.min(rows.length - 1, Math.round(t)));
    };
    const show = (e) => {
      const i = pick(e.clientX);
      const r = rows[i];
      cross.setAttribute("x1", X(i)); cross.setAttribute("x2", X(i));
      cross.setAttribute("opacity", ".5");
      tip.hidden = false;
      tip.innerHTML = o.tooltip(r);
      const box = svg.getBoundingClientRect();
      const px = (X(i) / W) * box.width;
      tip.style.left = Math.max(0, Math.min(box.width - tip.offsetWidth, px - tip.offsetWidth / 2)) + "px";
      tip.style.top = "0px";
      if (o.onPick) o.onPick(r);
    };
    const hide = () => { tip.hidden = true; cross.setAttribute("opacity", 0); };
    wrap.addEventListener("pointermove", show);
    wrap.addEventListener("pointerdown", show);
    wrap.addEventListener("pointerleave", hide);
  }

  /* ---------------------------------------------------------- 今日結論 */
  /* 結論文字由 eos/summary.py 在收集時產生並存進快照。
     放在 Python 端而不是這裡，是為了讓它可被測試，
     並且 Phase 5 的 Email 通知能用同一份文字。 */
  function renderSummary(card, box, s) {
    if (!s || !s.headline) { card.hidden = true; return; }
    card.hidden = false;
    box.textContent = "";

    const head = el("p", "sum-head");
    head.textContent = s.headline;
    box.append(head);

    const block = (title, items, cls) => {
      if (!items || !items.length) return;
      const wrap = el("div", "sum-block " + (cls || ""));
      const t = el("div", "sum-title"); t.textContent = title;
      const ul = el("ul");
      items.forEach((x) => { const li = el("li"); li.textContent = x; ul.append(li); });
      wrap.append(t, ul);
      box.append(wrap);
    };
    block("改善", s.drivers);
    block("拖累", s.drags);
    block("佐證", s.evidence);
    block("資料品質", s.quality, "warn");
    block("待觀察", s.watch);
  }

  /* ---------------------------------------------------------- 期間切換 */
  /* 資料還不夠長時，較長的期間選項取到的結果與較短的完全相同 ——
     按鈕看起來能按卻沒反應，會被當成壞掉。直接停用並說明原因。 */
  function syncRangeButtons(total) {
    document.querySelectorAll("[data-range]").forEach((b) => {
      if (b.dataset.range === "all") return;
      const n = Number(b.dataset.range);
      const short = total < n;
      b.disabled = short;
      b.classList.toggle("is-off", short);
      b.title = short ? "目前僅累積 " + total + " 個交易日，尚不足 " + n + " 日" : "";
    });
  }

  function initRangeControls(onChange) {
    document.querySelectorAll("[data-range]").forEach((b) => {
      b.addEventListener("click", () => {
        if (b.disabled) return;
        document.querySelectorAll("[data-range]").forEach((x) => x.classList.remove("is-on"));
        b.classList.add("is-on");
        onChange(b.dataset.range === "all" ? "all" : Number(b.dataset.range));
      });
    });
  }

  function initTableToggles() {
    document.querySelectorAll("[data-table]").forEach((b) => {
      b.addEventListener("click", () => {
        const box = $("#" + b.dataset.table + "-table");
        if (!box) return;
        box.hidden = !box.hidden;
        b.textContent = box.hidden ? "表格" : "收合";
      });
    });
  }

  /* ---------------------------------------------------------- 跨頁摘要 */
  /* 另一個標的的一句話近況。00881 的漲跌有一半是大盤的事，
     反過來也一樣 —— 兩頁互相帶一張小卡，不必來回切換才知道對面在哪個位置。
     讀的是同一份 data/eos_history_*.json，沒有額外的資料需求。 */
  async function crossSummary(card, body, instrument, opt) {
    const o = Object.assign({ label: "分數", rankOf: () => 3 }, opt || {});
    let rows;
    try {
      rows = await loadJSON("data/eos_history_" + instrument + ".json");
    } catch {
      card.hidden = true;                 // 對面還沒有資料就整張不顯示，不留空卡
      return;
    }
    rows = (rows || []).filter((r) => r && r.date && r.eos != null);
    if (!rows.length) { card.hidden = true; return; }

    const last = rows[rows.length - 1];
    const prev = rows[rows.length - 2];
    body.textContent = "";

    const row = el("div", "cross-row");
    const fig = el("div", "cross-figure");
    fig.textContent = last.eos;
    const side = el("div", "cross-side");

    const chip = el("span", "chip rating");
    chip.textContent = last.rating || "—";
    chip.style.background = "var(--rank-" + o.rankOf(last.rating) + ")";
    side.append(chip);

    const sub = el("div", "subtle");
    const bits = [last.date];
    if (prev && prev.eos != null) {
      const d = last.eos - prev.eos;
      bits.push((d > 0 ? "▲ " : d < 0 ? "▼ " : "－ ") + Math.abs(d));
    }
    sub.textContent = bits.join("　");
    side.append(sub);

    row.append(fig, side);
    body.append(row);

    if (o.note) {
      const n = el("p", "note");
      n.textContent = o.note(last);
      body.append(n);
    }
    card.hidden = false;
  }

  /* ---------------------------------------------------------- 資料 */
  async function loadJSON(url) {
    const res = await fetch(url, { cache: "no-cache" });
    if (!res.ok) throw new Error(url + " -> " + res.status);
    return res.json();
  }

  function registerSW() {
    if (!("serviceWorker" in navigator)) return;
    addEventListener("load", () => {
      navigator.serviceWorker.register("sw.js")
        .catch(() => { /* 離線功能失效，不影響主要內容 */ });
    });
  }

  return { $, el, rect, fmt, pct, signed, smart, table, initTheme, barList,
           scoreChart, renderSummary, crossSummary, syncRangeButtons,
           initRangeControls, initTableToggles, loadJSON, registerSW };
})();
