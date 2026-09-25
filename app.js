/* 00881 EOS 追蹤 — 手機優先的靜態前端。
   刻意不引入任何外部函式庫：圖表是手寫 SVG。
   理由有二：離線可用（Service Worker 只要快取自家檔案），
   以及不依賴 CDN —— 這個專案已經被 Stooq 與台銀的機器人驗證教訓過一次。 */
(() => {
  "use strict";

  const INSTRUMENT = "00881";
  const HISTORY_URL = `data/eos_history_${INSTRUMENT}.json`;
  const DAILY_URL = (d) => `data/daily/${INSTRUMENT}/${d}.json`;

  const DIMS = {
    A: "價格／折溢價", B: "含息趨勢／回檔", C: "成分股廣度",
    D: "海外科技", E: "波動／風險", F: "量價／法人",
  };
  const DIM_MAX = { A: 15, B: 25, C: 20, D: 15, E: 15, F: 10 };
  const RANK = { "高風險區": 1, "偏不利": 2, "中性等待": 3, "偏有利": 4, "高機會區": 5 };
  const THRESHOLDS = [35, 50, 65, 80];
  const STATUS_LABEL = {
    confirmed: "資料完整", provisional: "暫定（部分缺值）", insufficient: "資料不足",
  };

  const $ = (sel) => document.querySelector(sel);
  const el = (tag, cls) => { const n = document.createElement(tag); if (cls) n.className = cls; return n; };
  const fmt = (v, d = 2) =>
    v === null || v === undefined ? "—" :
    typeof v === "boolean" ? (v ? "是" : "否") :
    typeof v === "number" ? (Number.isInteger(v) ? String(v) : v.toFixed(d)) : String(v);
  const pct = (v, d = 2) => (v === null || v === undefined ? "—" : (v * 100).toFixed(d) + "%");

  /* 依量級決定小數位。成交量寫成 18245.2160 只是雜訊，
     而 RSI 或折溢價需要看到小數 —— 固定位數兩邊都討好不了。 */
  function smart(v) {
    if (v === null || v === undefined) return "—";
    if (typeof v === "boolean") return v ? "是" : "否";
    // 防呆：結構化欄位不該被當成單一數值輸出，否則會變成 [object Object]
    if (Array.isArray(v)) return `${v.length} 筆明細`;
    if (typeof v === "object") return "結構化資料";
    if (typeof v !== "number") return String(v);
    const a = Math.abs(v);
    if (Number.isInteger(v)) return v.toLocaleString("en-US");
    if (a >= 1000) return v.toLocaleString("en-US", { maximumFractionDigits: 0 });
    if (a >= 100) return v.toFixed(1);
    if (a >= 1) return v.toFixed(2);
    return v.toFixed(4);
  }

  let history = [];
  let meta = null;
  let range = 30;

  /* ---------------------------------------------------------- 主題切換 */
  function initTheme() {
    let saved = null;
    try { saved = localStorage.getItem("theme"); } catch { /* 私密瀏覽會丟例外 */ }
    if (saved === "dark" || saved === "light") document.documentElement.dataset.theme = saved;
    $("#theme-toggle").addEventListener("click", () => {
      const cur = document.documentElement.dataset.theme;
      const isDark = cur ? cur === "dark"
        : matchMedia("(prefers-color-scheme: dark)").matches;
      const next = isDark ? "light" : "dark";
      document.documentElement.dataset.theme = next;
      try { localStorage.setItem("theme", next); } catch { /* 忽略：僅影響偏好記憶 */ }
      render();
    });
  }

  /* ---------------------------------------------------------- 今日分數 */
  function renderHero(row, prev) {
    $("#hero-eos").textContent = row.eos ?? "--";

    const chip = $("#hero-rating");
    const label = row.rating || (row.status === "insufficient" ? "未出分" : "—");
    chip.textContent = label;
    chip.style.background = `var(--rank-${RANK[row.rating] || 3})`;
    chip.style.opacity = row.rating ? "1" : ".45";

    const cov = $("#hero-coverage");
    cov.textContent = `${STATUS_LABEL[row.status] || row.status} ${fmt(row.coverage, 0)}/100`;
    cov.dataset.s = row.status || "";

    const d = $("#hero-delta");
    if (row.eos != null && prev && prev.eos != null) {
      const diff = row.eos - prev.eos;
      const sign = diff > 0 ? "▲" : diff < 0 ? "▼" : "－";
      d.textContent = `${sign} ${Math.abs(diff)}　較 ${prev.date.slice(5)}`;
    } else {
      d.textContent = "";
    }

    const note = $("#hero-note");
    if (row.status === "insufficient") {
      note.textContent = `當日可計分構面僅 ${fmt(row.coverage, 0)}/100，依規則不計算 EOS。已取得的構面仍顯示於下方。`;
    } else if (row.status === "provisional") {
      note.textContent = "部分欄位尚未取得（常見為正式 NAV 當晚未發布）。回補後分數會自動更新為確定值。";
    } else {
      note.textContent = "";
    }
  }

  /* ---------------------------------------------------------- 六構面長條 */
  function renderDims(row) {
    const wrap = $("#dims");
    wrap.textContent = "";
    const W = 100, H = 10;                    // 以 viewBox 百分比座標作圖

    for (const [k, name] of Object.entries(DIMS)) {
      const max = DIM_MAX[k];
      const earned = row[k];
      const avail = row[`${k}_avail`];        // 目前歷史檔未帶，保留擴充

      const line = el("div", "dim-row");
      const n = el("div", "dim-name"); n.textContent = `${k}　${name}`;
      const v = el("div", "dim-val");
      // 工作表的「滿分／今日／得分率」三欄，在窄畫面壓成一行
      v.textContent = earned == null
        ? `—／${max}`
        : `${earned.toFixed(1)}／${max}　${Math.round((earned / max) * 100)}%`;
      line.append(n, v);

      const barBox = el("div", "dim-bar");
      const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
      svg.setAttribute("preserveAspectRatio", "none");
      svg.setAttribute("height", "10");
      svg.setAttribute("role", "img");
      svg.setAttribute("aria-label",
        `${name} ${earned == null ? "缺值" : earned.toFixed(1)} 分，滿分 ${max} 分`);

      const track = rect(0, 0, W, H, "var(--track)", 3);
      svg.append(track);
      if (earned != null && earned > 0) {
        // 資料端 4px 圓角、錨在基線；此處以 viewBox 比例換算
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

  function rect(x, y, w, h, fill, r) {
    const n = document.createElementNS("http://www.w3.org/2000/svg", "rect");
    n.setAttribute("x", x); n.setAttribute("y", y);
    n.setAttribute("width", Math.max(0, w)); n.setAttribute("height", h);
    n.setAttribute("fill", fill);
    if (r) { n.setAttribute("rx", r); n.setAttribute("ry", r); }
    return n;
  }

  /* ---------------------------------------------------------- 走勢圖 */
  function renderChart(rows) {
    const wrap = $("#chart-wrap");
    wrap.textContent = "";
    if (!rows.length) { wrap.textContent = "尚無資料"; return; }

    const W = 340, H = 190, P = { t: 10, r: 30, b: 22, l: 26 };
    const iw = W - P.l - P.r, ih = H - P.t - P.b;
    const NS = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(NS, "svg");
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    svg.setAttribute("role", "img");
    svg.setAttribute("aria-label", `EOS 走勢，共 ${rows.length} 個交易日`);

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
    for (const t of THRESHOLDS) {
      add("line", { x1: P.l, x2: P.l + iw, y1: Y(t), y2: Y(t),
                    stroke: "var(--grid)", "stroke-width": 1 });
      add("text", { x: P.l + iw + 4, y: Y(t) + 3, "font-size": 8,
                    fill: "var(--muted)" }, String(t));
    }
    add("line", { x1: P.l, x2: P.l + iw, y1: Y(0), y2: Y(0),
                  stroke: "var(--axis)", "stroke-width": 1 });
    // 標出 0，讓最下面那條線讀起來是刻度的一部分而不是游離的橫線。
    // y 軸固定 0–100 不做截斷：EOS 是有界分數，截斷會放大視覺上的起伏。
    add("text", { x: P.l + iw + 4, y: Y(0) + 3, "font-size": 8,
                  fill: "var(--muted)" }, "0");

    // 線段：遇到未出分的日子斷開，不跨越缺口連線
    let seg = [];
    const flush = () => {
      if (seg.length > 1) {
        add("path", { d: seg.map((p, i) => `${i ? "L" : "M"}${p[0]} ${p[1]}`).join(" "),
                      fill: "none", stroke: "var(--series-1)", "stroke-width": 2,
                      "stroke-linejoin": "round", "stroke-linecap": "round" });
      } else if (seg.length === 1) {
        add("circle", { cx: seg[0][0], cy: seg[0][1], r: 2, fill: "var(--series-1)" });
      }
      seg = [];
    };
    rows.forEach((r, i) => {
      if (r.eos == null) { flush(); return; }
      seg.push([X(i), Y(r.eos)]);
    });
    flush();

    // 標記點：實心＝確定，空心＝暫定（形狀作為色彩以外的第二編碼）
    rows.forEach((r, i) => {
      if (r.eos == null) return;
      const solid = r.status === "confirmed";
      add("circle", {
        cx: X(i), cy: Y(r.eos), r: 3.2,
        fill: solid ? "var(--series-1)" : "var(--surface-1)",
        stroke: "var(--series-1)", "stroke-width": solid ? 0 : 2,
      });
    });

    // 端點直接標值（只標端點，不是每個點都掛數字）
    const last = [...rows].reverse().find((r) => r.eos != null);
    if (last) {
      const li = rows.indexOf(last);
      const atEdge = li > rows.length - 3;
      // 靠右邊時往左推 9px：不然端點值會和右側的門檻刻度擠在一起，
      // 讀起來像「58 65」兩個數字連在一起。
      add("text", { x: atEdge ? X(li) - 9 : X(li), y: Y(last.eos) - 9,
                    "font-size": 10, "font-weight": 600,
                    "text-anchor": atEdge ? "end" : "middle",
                    fill: "var(--ink)" }, String(last.eos));
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
    attachHover(wrap, svg, tip, cross, rows, X, W, P, iw);
  }

  /* 指標／觸控共用的最近點命中層。手指的命中區必須遠大於 3px 的點。 */
  function attachHover(wrap, svg, tip, cross, rows, X, W, P, iw) {
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
      tip.innerHTML =
        `${r.date}<br>EOS <b>${r.eos ?? "—"}</b>` +
        `（${r.rating || STATUS_LABEL[r.status] || "—"}）<br>` +
        `<span class="subtle">覆蓋率 ${fmt(r.coverage, 0)}　收盤 ${fmt(r.close)}</span>`;
      const box = svg.getBoundingClientRect();
      const px = (X(i) / W) * box.width;
      tip.style.left = Math.max(0, Math.min(box.width - tip.offsetWidth, px - tip.offsetWidth / 2)) + "px";
      tip.style.top = "0px";
      $("#day-picker").value = r.date;
      loadDetail(r.date);
    };
    const hide = () => { tip.hidden = true; cross.setAttribute("opacity", 0); };
    wrap.addEventListener("pointermove", show);
    wrap.addEventListener("pointerdown", show);
    wrap.addEventListener("pointerleave", hide);
  }

  /* ---------------------------------------------------------- 表格檢視 */
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

  /* 前十大持股：對齊工作表的成分股分頁。
     只顯示彙總的 WCR 看不出是誰拖累的 —— 逐檔攤開才有診斷價值。 */
  /* 今日結論：由 eos/summary.py 在收集時產生並存進快照。
     放在 Python 端而不是這裡，是為了讓它可被測試，
     並且 Phase 5 的 Email 通知能用同一份文字。 */
  function renderSummary(row, snap) {
    const card = $("#summary-card");
    const box = $("#summary");
    const s = ((snap || {}).eos || {}).summary;
    if (!s || !s.headline) { card.hidden = true; return; }
    card.hidden = false;
    box.textContent = "";

    const head = el("p", "sum-head");
    head.textContent = s.headline;
    box.append(head);

    const block = (title, items, cls) => {
      if (!items || !items.length) return;
      const wrap = el("div", `sum-block ${cls || ""}`);
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

  function renderHoldings(snap) {
    const box = $("#holdings");
    const cap = $("#holdings-asof");
    box.textContent = "";
    const f = (snap.fields || {}).top_holdings;
    const rows = f && Array.isArray(f.value) ? f.value : null;
    if (!rows || !rows.length) {
      box.textContent = "該日未取得成分股明細。";
      cap.textContent = "";
      $("#holdings-chart").textContent = "";
      return;
    }
    renderContribChart(rows);
    cap.textContent = f.status === "stale" ? `權重 ${f.as_of}（非當日）` : `權重 ${f.as_of}`;
    cap.className = f.status === "stale" ? "flag" : "subtle";
    if (f.status === "stale") cap.dataset.s = "stale";

    const body = rows.map((r) => [
      { node: nameCell(r) },
      { node: document.createTextNode(pct(r.weight, 2)), cls: "num" },
      { node: document.createTextNode(smart(r.close)), cls: "num" },
      { node: retCell(r.ret), cls: "num" },
      { node: retCell(r.contrib, 3), cls: "num" },
    ]);
    const up = rows.filter((r) => r.ret > 0).length;
    const sumW = rows.reduce((a, r) => a + r.weight, 0);
    const sumC = rows.reduce((a, r) => a + r.contrib, 0);
    body.push([
      { node: document.createTextNode("合計") },
      { node: document.createTextNode(pct(sumW, 2)), cls: "num" },
      { node: document.createTextNode(`${up} 漲 / ${rows.length - up} 跌`), cls: "num" },
      { node: document.createTextNode("") },
      { node: retCell(sumC, 3), cls: "num" },
    ]);

    const t = table(["成分股", "權重", "收盤", "漲跌幅", "加權貢獻"], body);
    t.classList.add("holdings");
    box.append(t);
  }

  /* Top10 權重×漲跌貢獻：以零線為中心的橫條。

     刻意不用顏色區分正負 —— 條的方向已經把正負講完了，再加顏色是重複編碼；
     而且台股紅漲綠跌與國際慣例相反，用顏色反而製造誤讀。
     手機上用橫條而非直條，中文名才能水平閱讀，不必轉九十度。 */
  function renderContribChart(rows) {
    const wrap = $("#holdings-chart");
    wrap.textContent = "";
    if (!rows || !rows.length) return;

    const NS = "http://www.w3.org/2000/svg";
    const rowH = 20, gap = 4;
    const P = { t: 8, r: 8, b: 16, l: 62 };
    const W = 340;
    const H = P.t + rows.length * (rowH + gap) - gap + P.b;
    const iw = W - P.l - P.r;
    const zero = P.l + iw / 2;

    const maxAbs = Math.max(...rows.map((r) => Math.abs(r.contrib)), 1e-6);
    const X = (v) => zero + (v / maxAbs) * (iw / 2 - 26);   // 留空間給極值標籤

    const svg = document.createElementNS(NS, "svg");
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    svg.setAttribute("role", "img");
    svg.setAttribute("aria-label", "前十大持股的權重加權貢獻");
    const add = (tag, attrs, text) => {
      const n = document.createElementNS(NS, tag);
      for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
      if (text != null) n.textContent = text;
      svg.append(n); return n;
    };

    const best = rows.reduce((a, b) => (b.contrib > a.contrib ? b : a));
    const worst = rows.reduce((a, b) => (b.contrib < a.contrib ? b : a));

    rows.forEach((r, i) => {
      const y = P.t + i * (rowH + gap);
      add("text", { x: P.l - 6, y: y + rowH / 2 + 3.5, "font-size": 9,
                    "text-anchor": "end", fill: "var(--ink-2)" }, r.name);

      const x = X(r.contrib);
      const w = Math.max(1.5, Math.abs(x - zero));
      add("rect", {
        x: r.contrib >= 0 ? zero : zero - w, y: y + 3,
        width: w, height: rowH - 6, rx: 2, ry: 2, fill: "var(--series-1)",
      });

      // 只標極值；其餘由下方表格提供完整數字（表格即 table view）
      if (r === best || r === worst) {
        const pos = r.contrib >= 0;
        const text = `${(r.contrib * 100).toFixed(3)}%`;
        const est = text.length * 4.6;            // 概估字寬，用來判斷會不會撞到名稱欄
        const outside = pos ? x + 4 : x - 4;
        // 外側放不下就翻到長條內側，否則負值極值會壓到左邊的成分股名稱
        const collides = !pos && outside - est < P.l + 2;
        add("text", {
          x: collides ? x + 4 : outside,
          y: y + rowH / 2 + 3.5, "font-size": 8.5, "font-weight": 600,
          "text-anchor": collides ? "start" : (pos ? "start" : "end"),
          fill: collides ? "var(--surface-1)" : "var(--ink)",
        }, text);
      }
    });

    // 零線畫在最後，才不會被長條蓋住
    add("line", { x1: zero, x2: zero, y1: P.t - 2, y2: H - P.b + 2,
                  stroke: "var(--axis)", "stroke-width": 1 });
    add("text", { x: zero, y: H - 4, "font-size": 8, "text-anchor": "middle",
                  fill: "var(--muted)" }, "0");

    wrap.append(svg);

    const tip = el("div", "tip"); tip.hidden = true; wrap.append(tip);
    const show = (e) => {
      const box = svg.getBoundingClientRect();
      const vy = ((e.clientY - box.top) / box.height) * H;
      const i = Math.floor((vy - P.t) / (rowH + gap));
      if (i < 0 || i >= rows.length) { tip.hidden = true; return; }
      const r = rows[i];
      tip.hidden = false;
      tip.innerHTML = `${r.name}（${r.code}）<br>權重 <b>${pct(r.weight, 2)}</b>　`
        + `漲跌 <b>${(r.ret * 100).toFixed(2)}%</b><br>`
        + `貢獻 <b>${(r.contrib * 100).toFixed(3)}%</b>`;
      tip.style.left = "8px";
      tip.style.top = Math.max(0, (i * (rowH + gap) / H) * box.height - 6) + "px";
    };
    wrap.addEventListener("pointermove", show);
    wrap.addEventListener("pointerdown", show);
    wrap.addEventListener("pointerleave", () => { tip.hidden = true; });
  }

  function nameCell(r) {
    const d = el("div");
    const n = el("div", "fname"); n.textContent = r.name;
    const c = el("div", "src"); c.textContent = r.code;
    d.append(n, c);
    return d;
  }

  /* 漲跌以符號與文字標示，不靠顏色 —— 台股紅漲綠跌與國際慣例相反，
     用顏色反而會讓不同習慣的讀者讀錯方向。 */
  function retCell(v, d = 2) {
    const s = el("span");
    if (v === null || v === undefined) { s.textContent = "—"; return s; }
    const sign = v > 0 ? "▲" : v < 0 ? "▼" : "－";
    s.textContent = `${sign}${Math.abs(v * 100).toFixed(d)}%`;
    s.className = "ret";
    return s;
  }

  function renderHistTable(rows) {
    const box = $("#hist-table"); box.textContent = "";
    box.append(table(["日期", "EOS", "評級", "覆蓋率", "收盤"],
      [...rows].reverse().map((r) => [
        r.date,
        { node: document.createTextNode(r.eos ?? "—"), cls: "num" },
        r.rating || STATUS_LABEL[r.status] || "—",
        { node: document.createTextNode(fmt(r.coverage, 0)), cls: "num" },
        { node: document.createTextNode(fmt(r.close)), cls: "num" },
      ])));
  }

  /* ---------------------------------------------------------- 當日明細 */
  const PCT_FIELDS = new Set(["premium", "ret5", "ret20", "ret60", "drawdown20",
    "drawdown60", "rv20", "wcr", "sox_ret", "ndx_ret", "tsm_ret", "nvda_ret", "twd_change",
    "vs_ex_ref", "to_full_recovery", "day_change"]);

  /* 欄位說明。原始欄位名是程式識別字，對照 eos_rubric_v1.1.yaml 時有用，
     但不該是使用者看到的主要文字。dim 標出這個欄位餵給哪個構面，
     沒有 dim 的是參考資訊，不進計分。 */
  /* o = 組內排序。刻意手動指定而非依字面排序 ——
     照中文字面排會得到「20日均量→成交值→成交量→收盤價」這種沒有邏輯的順序。
     順序對齊既有工作表的「正式收盤與基金資料」與「價格/技術位置」兩個區塊。 */
  const FIELD_META = {
    // 淨值（工作表左區塊尾端）
    nav:      { g: "淨值與折溢價", o: 1, label: "正式淨值 NAV", unit: "元" },
    premium:  { g: "淨值與折溢價", o: 2, label: "正式折溢價", dim: "A" },

    // 價格位置（由收盤價與除息基準推算，非收集而來）
    vs_ex_ref:        { g: "價格位置", o: 1, label: "相對除息參考價 50.55" },
    to_full_recovery: { g: "價格位置", o: 2, label: "距 55.15 剩餘漲幅" },

    // 價格與成交 —— 工作表順序：收盤 開盤 最高 最低 漲跌幅 成交量 成交值
    close:        { g: "價格與成交", o: 1, label: "收盤價", unit: "元" },
    open:         { g: "價格與成交", o: 2, label: "開盤價", unit: "元" },
    high:         { g: "價格與成交", o: 3, label: "最高價", unit: "元" },
    low:          { g: "價格與成交", o: 4, label: "最低價", unit: "元" },
    day_change:   { g: "價格與成交", o: 5, label: "漲跌幅" },
    volume_lots:  { g: "價格與成交", o: 6, label: "成交量", unit: "張" },
    turnover_100m:{ g: "價格與成交", o: 7, label: "成交值", unit: "億元" },
    avg_vol20:    { g: "價格與成交", o: 8, label: "20 日平均量", unit: "張" },
    volume_ratio: { g: "價格與成交", o: 9, label: "全日量比", unit: "倍", dim: "F" },
    day_direction:{ g: "價格與成交", o: 10, label: "當日漲跌方向", dim: "F" },

    // 含息技術面 —— 工作表順序：5/20/60日總報酬 MA20/60/120狀態 RSI14 20日高點回檔
    ret5:               { g: "含息技術面", o: 1, label: "5 日總報酬" },
    ret20:              { g: "含息技術面", o: 2, label: "20 日總報酬" },
    ret60:              { g: "含息技術面", o: 3, label: "60 日總報酬" },
    close_adj_gt_ma20:  { g: "含息技術面", o: 4, label: "MA20 狀態", dim: "B" },
    ma20_gt_ma60:       { g: "含息技術面", o: 5, label: "MA60 狀態（MA20 在 MA60 之上）", dim: "B" },
    close_adj_gt_ma120: { g: "含息技術面", o: 6, label: "MA120 狀態", dim: "B" },
    ret60_positive:     { g: "含息技術面", o: 7, label: "60 日總報酬為正", dim: "B" },
    rsi14:      { g: "含息技術面", o: 8, label: "RSI14", dim: "B" },
    drawdown20: { g: "含息技術面", o: 9, label: "20 日高點回檔", dim: "B" },
    drawdown60: { g: "含息技術面", o: 10, label: "60 日高點回檔" },
    rv20:       { g: "含息技術面", o: 11, label: "RV20（年化實現波動率）", dim: "B" },
    // 以下為支撐上列判斷的原始值，工作表未列，排在後面
    close_adj:  { g: "含息技術面", o: 20, label: "含息調整價", unit: "元" },
    ma20:       { g: "含息技術面", o: 21, label: "20 日均線", unit: "元" },
    ma60:       { g: "含息技術面", o: 22, label: "60 日均線", unit: "元" },
    ma120:      { g: "含息技術面", o: 23, label: "120 日均線", unit: "元" },

    // 成分股
    wcr:           { g: "成分股廣度", o: 1, label: "Top10 加權貢獻", dim: "C" },
    breadth_count: { g: "成分股廣度", o: 2, label: "Top10 上漲家數", unit: "／10 檔", dim: "C" },

    // 海外 —— 工作表順序：SOX Nasdaq TSM ADR NVDA
    sox_ret:  { g: "海外科技（前一美股時段）", o: 1, label: "SOX", dim: "D" },
    ndx_ret:  { g: "海外科技（前一美股時段）", o: 2, label: "Nasdaq", dim: "D" },
    tsm_ret:  { g: "海外科技（前一美股時段）", o: 3, label: "TSM ADR", dim: "D" },
    nvda_ret: { g: "海外科技（前一美股時段）", o: 4, label: "NVDA", dim: "D" },

    // 風險環境 —— 工作表順序：VIX US10Y USD/TWD
    vix:        { g: "風險環境", o: 1, label: "VIX", dim: "E" },
    us10y:      { g: "風險環境", o: 2, label: "US10Y", unit: "%", dim: "E" },
    usdtwd:     { g: "風險環境", o: 3, label: "USD/TWD" },
    twd_change: { g: "風險環境", o: 4, label: "台幣日變動（負值為升值）", dim: "E" },

    // 法人 —— 工作表順序：外資台股買賣超 三大法人買賣超
    foreign_net_100m:             { g: "法人資金流", o: 1, label: "外資台股買賣超", unit: "億元" },
    institutional_net:            { g: "法人資金流", o: 2, label: "三大法人買賣超", unit: "億元", dim: "F" },
    stock_foreign_net_lots:       { g: "法人資金流", o: 3, label: "00881 外資買賣超", unit: "張" },
    stock_institutional_net_lots: { g: "法人資金流", o: 4, label: "00881 三大法人買賣超", unit: "張" },
  };
  const GROUP_ORDER = ["淨值與折溢價", "價格位置", "價格與成交", "含息技術面", "成分股廣度",
                       "海外科技（前一美股時段）", "風險環境", "法人資金流", "其他"];

  /* 不列進明細的欄位：
     top_holdings —— 物件陣列，已由「前十大持股」表呈現；
                     硬塞進單值欄位會變成 [object Object] 並撐爆版面
     institutional_net_100m —— 與 institutional_net 同值，
                     後者是 rubric 實際讀取的名稱，列兩次只是雜訊 */
  const RENDERED_ELSEWHERE = new Set(["top_holdings", "institutional_net_100m"]);

  /* 判讀：把數字翻成一句話，對齊工作表「判讀」欄的用語。
     門檻與 eos_rubric_v1.1.yaml 一致，但這裡只負責呈現，不參與計分。 */
  const band = (v, pairs) => {
    for (const [lt, text] of pairs) if (lt === null || v < lt) return text;
    return pairs[pairs.length - 1][1];
  };
  const US_BAND = [[-0.015, "強負向"], [-0.005, "負向"], [0.005, "持平"],
                   [0.015, "正向"], [null, "強正向"]];

  const INTERPRET = {
    premium: (v) => band(v, [[-0.005, "明顯折價"], [-0.001, "小幅折價"],
                             [0.003, "接近淨值"], [0.008, "溢價"], [null, "高溢價"]]),
    sox_ret: (v) => band(v, US_BAND), ndx_ret: (v) => band(v, US_BAND),
    tsm_ret: (v) => band(v, US_BAND), nvda_ret: (v) => band(v, US_BAND),
    vix: (v) => band(v, [[15, "低波動"], [18, "偏低"], [22, "中性"], [28, "偏高"], [null, "高波動"]]),
    us10y: (v) => band(v, [[4, "寬鬆"], [4.5, "中性"], [4.8, "偏高"], [5, "高"], [null, "明顯偏高"]]),
    twd_change: (v) => band(v, [[-0.003, "台幣明顯升值"], [0, "台幣升值"],
                                [0.003, "台幣貶值"], [null, "台幣明顯貶值"]]),
    rsi14: (v) => band(v, [[30, "超賣"], [40, "偏弱"], [55, "中性"], [65, "偏強"],
                           [75, "強勢"], [null, "過熱"]]),
    rv20: (v) => band(v, [[0.2, "波動收斂"], [0.25, "中性"], [0.32, "偏高"], [null, "高波動"]]),
    drawdown20: (v) => band(Math.abs(v), [[0.01, "貼近高點"], [0.03, "小幅回檔"],
                                          [0.08, "健康回檔"], [0.15, "深度回檔"], [null, "趨勢轉弱"]]),
    institutional_net: (v) => band(v, [[-300, "大幅賣超"], [-100, "賣超"], [100, "中性"],
                                       [300, "買超"], [null, "大幅買超"]]),
    foreign_net_100m: (v) => band(v, [[-300, "大幅賣超"], [-100, "賣超"], [100, "中性"],
                                      [300, "買超"], [null, "大幅買超"]]),
    wcr: (v) => (v > 0.0005 ? "正向" : v < -0.0005 ? "負向" : "持平"),
    breadth_count: (v) => `${v} / 10 檔上漲`,
    vs_ex_ref: (v) => (v >= 0 ? "已站上除息參考價" : "仍低於除息參考價"),
    to_full_recovery: (v) => (v <= 0 ? "已完成填息" : `距完整填息尚需 ${(v * 100).toFixed(2)}%`),
  };

  function interpret(name, f, snap) {
    if (f.value === null || f.value === undefined) return "";
    if (name === "volume_ratio") {
      const dir = (snap.fields.day_direction || {}).value;
      if (!dir) return "";
      const heavy = f.value >= 1.2, light = f.value < 0.5;
      return dir === "up"
        ? (heavy ? "放量上漲（確認）" : light ? "無量上漲" : "量能普通")
        : (heavy ? "放量下跌" : light ? "縮量下跌（賣壓不重）" : "量能普通");
    }
    if (name.endsWith("_gt_ma20") || name.endsWith("_gt_ma60") || name.endsWith("_gt_ma120"))
      return f.value ? "站上" : "跌破";
    if (name === "ret60_positive") return f.value ? "60 日為正報酬" : "60 日為負報酬";
    const fn = INTERPRET[name];
    return fn ? fn(f.value) : "";
  }

  /* 除息參考價與填息目標是設定值，不是每天收集來的資料，
     因此在前端由收盤價推算，而不是在 30 份快照裡各存一次。
     填息目標只是市場心理標記，不是合理價值（v1.0 文件 5.）。 */
  function addPricePosition(snap) {
    const close = (snap.fields.close || {}).value;

    // 漲跌幅：工作表有這一列，但平台不需要另存一個欄位 ——
    // 由歷史序列的前一交易日收盤推算即可
    const i = history.findIndex((r) => r.date === snap.trade_date);
    const prevClose = i > 0 ? history[i - 1].close : null;
    if (close && prevClose) {
      snap.fields.day_change = {
        value: close / prevClose - 1, status: "ok", as_of: snap.trade_date,
        source: `由前一交易日收盤 ${prevClose} 元推算`, url: "", note: "",
      };
    }

    const ex = (meta && meta.ex_dividend) || {};
    if (!close || !ex.reference_price || !ex.full_recovery) return;
    const base = {
      source: `由收盤價推算（除息 ${ex.date}，配息 ${ex.cash} 元）`,
      url: "", as_of: snap.trade_date, status: "ok", note: "",
    };
    snap.fields.vs_ex_ref = { ...base, value: close / ex.reference_price - 1 };
    snap.fields.to_full_recovery = { ...base, value: ex.full_recovery / close - 1 };
  }

  async function loadDetail(day) {
    const box = $("#detail");
    let snap;
    try {
      const res = await fetch(DAILY_URL(day), { cache: "no-cache" });
      if (!res.ok) throw new Error(res.status);
      snap = await res.json();
    } catch {
      box.textContent = "該日快照尚未取得。";
      return;
    }
    /* 375px 寬放不下四欄，硬塞會把「資料日期」切掉。
       改為兩欄：左邊中文說明 + 原始欄位名/來源小字，右邊數值 + 狀態標記。
       並依構面分組，讓「這個數字餵給哪一段模型」一眼看得出來。 */
    // 必須在建立分組前補上推算欄位，否則它們不會被分進任何一組
    addPricePosition(snap);

    const fields = snap.fields || {};
    const groups = new Map();
    for (const name of Object.keys(fields)) {
      if (RENDERED_ELSEWHERE.has(name)) continue;
      const meta = FIELD_META[name] || { g: "其他", label: name };
      if (!groups.has(meta.g)) groups.set(meta.g, []);
      groups.get(meta.g).push([name, meta]);
    }

    renderHoldings(snap);
    // 結論只在看最新交易日時顯示；翻閱歷史日時顯示該日自己的結論
    renderSummary(null, snap);

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
          const f = fields[name];

          const left = el("div");
          const nm = el("div", "fname");
          nm.textContent = meta.label;
          if (meta.dim) {
            const tag = el("span", "dimtag");
            tag.textContent = meta.dim;
            tag.title = `此欄位計入 ${meta.dim} 構面`;
            nm.append(tag);
          }
          const src = el("div", "src");
          src.textContent = `${name}　${f.as_of || "—"}　${f.source || ""}`.trim();
          left.append(nm, src);
          if (f.note) {
            const note = el("div", "src"); note.textContent = f.note; left.append(note);
          }

          const right = el("div");
          const val = el("div");
          const shown = PCT_FIELDS.has(name) ? pct(f.value) : smart(f.value);
          val.textContent = meta.unit && f.value !== null && f.value !== undefined
            ? `${shown} ${meta.unit}` : shown;
          right.append(val);

          const read = interpret(name, f, snap);
          if (read) { const r = el("div", "read"); r.textContent = read; right.append(r); }

          const flag = el("span", "flag");
          flag.dataset.s = f.status; flag.textContent = f.status;
          right.append(flag);

          return [{ node: left }, { node: right, cls: "num" }];
        });
      box.append(table(["項目", "值"], rows));
    }
  }

  /* ---------------------------------------------------------- 組裝 */
  function slice() {
    if (range === "all") return history;
    return history.slice(-Number(range));
  }

  function render() {
    const rows = slice();
    const latest = history[history.length - 1];
    const prev = history.filter((r) => r.eos != null).slice(-2)[0];
    if (latest) {
      renderHero(latest, prev && prev.date !== latest.date ? prev : null);
      renderDims(latest);
    }
    renderChart(rows);
    renderHistTable(rows);
    syncRangeButtons();
    const cap = $("#chart-count");
    if (cap) {
      cap.textContent = rows.length === history.length
        ? `顯示全部 ${rows.length} 個交易日`
        : `顯示最近 ${rows.length} 個交易日（累積 ${history.length}）`;
    }
  }

  /* 資料還不夠長時，較長的期間選項取到的結果與較短的完全相同 ——
     按鈕看起來能按卻沒反應，會被當成壞掉。直接停用並說明原因。 */
  function syncRangeButtons() {
    document.querySelectorAll("[data-range]").forEach((b) => {
      if (b.dataset.range === "all") return;
      const n = Number(b.dataset.range);
      const short = history.length < n;
      b.disabled = short;
      b.classList.toggle("is-off", short);
      b.title = short ? `目前僅累積 ${history.length} 個交易日，尚不足 ${n} 日` : "";
    });
  }

  function initControls() {
    document.querySelectorAll("[data-range]").forEach((b) => {
      b.addEventListener("click", () => {
        if (b.disabled) return;
        document.querySelectorAll("[data-range]").forEach((x) => x.classList.remove("is-on"));
        b.classList.add("is-on");
        range = b.dataset.range === "all" ? "all" : Number(b.dataset.range);
        render();
      });
    });
    document.querySelectorAll("[data-table]").forEach((b) => {
      b.addEventListener("click", () => {
        const id = b.dataset.table === "dims" ? "#dims-table" : "#hist-table";
        const box = $(id);
        box.hidden = !box.hidden;
        b.textContent = box.hidden ? "表格" : "收合";
      });
    });
    const picker = $("#day-picker");
    picker.addEventListener("change", () => loadDetail(picker.value));
  }

  async function main() {
    initTheme();
    try {
      const res = await fetch(HISTORY_URL, { cache: "no-cache" });
      history = await res.json();
    } catch {
      $("#fallback").textContent = "無法載入資料，且無離線快取可用。";
      return;
    }
    history = history.filter((r) => r && r.date).sort((a, b) => a.date.localeCompare(b.date));
    if (!history.length) { $("#fallback").textContent = "尚無任何每日快照。"; return; }

    try {
      const m = await fetch(`data/instrument_${INSTRUMENT}.json`, { cache: "no-cache" });
      if (m.ok) meta = await m.json();
    } catch { /* 沒有也不影響主要內容，只是少了價格位置兩列 */ }

    const picker = $("#day-picker");
    [...history].reverse().forEach((r) => {
      const o = el("option"); o.value = r.date; o.textContent = r.date; picker.append(o);
    });

    initControls();
    render();
    const latest = history[history.length - 1];
    $("#asof").textContent = `最後更新交易日 ${latest.date}　共 ${history.length} 個交易日`;
    await loadDetail(latest.date);

    $("#fallback").hidden = true;
    $("#app").hidden = false;
  }

  if ("serviceWorker" in navigator) {
    addEventListener("load", () => navigator.serviceWorker.register("sw.js").catch(() => {}));
  }
  main();
})();
