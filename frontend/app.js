const $ = (id) => document.getElementById(id);

const esc = (value) => {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/\"/g, "&quot;")
    .replace(/'/g, "&#39;");
};

const fmt = (value, fallback = "—") => {
  if (value === null || value === undefined || value === "") return fallback;
  return value;
};

const fmtPct = (value) => {
  return typeof value === "number" ? `${value.toFixed(1)}%` : fmt(value);
};

const fmtNum = (value, digits = 2) => {
  if (typeof value !== "number") return fmt(value);
  return value.toLocaleString(undefined, { maximumFractionDigits: digits });
};

const currencyLabelFor = (key, item) => {
  if (key === "SILVER") return "INR/kg";
  if (key === "GOLD") return "INR/10g";
  const type = item?.type;
  if (type === "CRYPTO") return "USD";
  if (type === "GLOBAL_STOCK") return "USD";
  if (type === "INDIA_STOCK") return "INR";
  if (type === "COMMODITY") return "USD";
  if (INDIA_INDICES.includes(key)) return "INR";
  if (GLOBAL_INDICES.includes(key)) return "USD";
  if (PAGE === "global" || PAGE === "commodities") return "USD";
  if (PAGE === "india") return "INR";
  return "";
};

const formatPriceValue = (value, key, item, digits = 2) => {
  if (typeof value !== "number") return fmt(value);
  const label = currencyLabelFor(key, item);
  const num = fmtNum(value, digits);
  return label ? `${num} ${label}` : num;
};

const renderList = (items, emptyLabel = "No data", maxItems = null) => {
  if (!Array.isArray(items) || items.length === 0) {
    return `<p class="muted">${esc(emptyLabel)}</p>`;
  }
  const sliced = maxItems ? items.slice(0, maxItems) : items;
  return `<ul class="list">${sliced.map((i) => `<li>${esc(i)}</li>`).join("")}</ul>`;
};

const renderMetric = (label, value, valueId = "") => {
  const idAttr = valueId ? ` id="${esc(valueId)}"` : "";
  return `
    <div class="metric">
      <label>${esc(label)}</label>
      <span${idAttr}>${esc(fmt(value))}</span>
    </div>
  `;
};

const displayLabel = (value) => {
  if (!value) return "";
  const raw = String(value).replace(/^\^/, "").replace(/\.NS$/i, "");
  const map = {
    SP500: "S&P 500",
    "SP500 FUT": "S&P 500 Futures",
    "NASDAQ100 FUT": "Nasdaq 100 Futures",
    "DOW FUT": "Dow Futures",
    "RUSSELL2000 FUT": "Russell 2000 Futures",
    "NIKKEI225 FUT": "Nikkei 225 Futures",
    NASDAQ: "NASDAQ Composite",
    DAX: "DAX 40",
    NIKKEI: "Nikkei 225",
    HANGSENG: "Hang Seng",
    INDIA_VIX: "India VIX",
    BANKNIFTY: "NIFTY Bank",
    NIFTY: "NIFTY 50",
    "NIFTY INDEX": "NIFTY 50",
    "BANK NIFTY INDEX": "NIFTY Bank",
    "SENSEX INDEX": "SENSEX",
    BSESN: "SENSEX"
  };
  return map[raw] || raw;
};

const buildStrategyUniverseLine = (strategy) => {
  const counts = strategy?.counts || {};
  const breakdown = counts?.universe_breakdown || {};
  const parts = [];
  if (Number.isFinite(counts.assets)) parts.push(`Tracked: ${counts.assets}`);
  if (Number.isFinite(counts.assets_with_data)) parts.push(`Live: ${counts.assets_with_data}`);
  const breakdownLabels = {
    fno_stocks: "F&O Stocks",
    index_companions: "Index Companions",
    index_futures: "Index Futures",
  };
  Object.entries(breakdownLabels).forEach(([key, label]) => {
    const value = breakdown?.[key];
    if (Number.isFinite(value) && value > 0) parts.push(`${label}: ${value}`);
  });
  if (Number.isFinite(counts.signals_total)) parts.push(`Signals: ${counts.signals_total}`);
  return parts.join(" | ");
};

const instrumentBadgeLabel = (item) => {
  const key = String(item?.instrument_type || "").trim().toLowerCase();
  const labels = {
    fno_stock: "F&O Stock",
    index_future: "Index Future",
    index_companion: "Index Companion",
  };
  return labels[key] || "";
};

const normalizeTicker = (value) => {
  if (!value) return "";
  return String(value)
    .trim()
    .toUpperCase()
    .replace(/^\^/, "")
    .replace(/\.NS$/i, "")
    .replace(/\.HK$/i, "")
    .replace(/\.DE$/i, "")
    .replace(/\.T$/i, "")
    .replace(/-USD$/i, "");
};

const cleanText = (value) => {
  if (!value) return "";
  return String(value)
    .replace(/\b([A-Z0-9]{1,12})\.NS\b/gi, "$1")
    .replace(/\^([A-Z0-9]+)/g, "$1");
};

const resolveAssetKey = (data, rawKey) => {
  if (!rawKey || !data) return null;
  if (data[rawKey]) return rawKey;
  const cleaned = String(rawKey).replace(/^\^/, "").replace(/\.NS$/i, "");
  if (data[cleaned]) return cleaned;
  const upper = cleaned.toUpperCase();
  const keys = Object.keys(data);
  for (const k of keys) {
    const label = displayLabel(k).toUpperCase();
    if (label === upper) return k;
  }
  for (const k of keys) {
    const label = displayLabel(k).toUpperCase();
    if (label.includes(upper)) return k;
  }
  return null;
};

const buildPageUniverse = (data, page) => {
  const set = new Set();
  const addKey = (key) => {
    if (!key) return;
    set.add(String(key).toUpperCase());
    set.add(normalizeTicker(key));
  };

  Object.entries(data || {}).forEach(([key, value]) => {
    if (page === "all" && ["INDIA_STOCK", "GLOBAL_STOCK", "COMMODITY", "CRYPTO", "INDEX"].includes(value?.type)) addKey(key);
    if (page === "global" && value?.type === "GLOBAL_STOCK") addKey(key);
    if (page === "commodities" && value?.type === "COMMODITY") addKey(key);
    if (page === "crypto" && value?.type === "CRYPTO") addKey(key);
    if (page === "india" && value?.type === "INDIA_STOCK") addKey(key);
  });

  if (page === "all") {
    INDIA_INDICES.forEach(addKey);
    GLOBAL_INDICES.forEach(addKey);
    COMMODITIES.forEach(addKey);
    CRYPTO.forEach(addKey);
  }
  if (page === "commodities") COMMODITIES.forEach(addKey);
  if (page === "crypto") CRYPTO.forEach(addKey);
  return set;
};

const isItemInPageUniverse = (item, universe) => {
  if (!item || !(universe instanceof Set) || universe.size === 0) return false;
  const candidates = [item.ticker, item.symbol, item.name]
    .filter(Boolean)
    .map((x) => String(x).trim())
    .filter(Boolean);
  return candidates.some((c) => {
    const up = c.toUpperCase();
    const norm = normalizeTicker(c);
    return universe.has(up) || universe.has(norm);
  });
};

const filterIntradayStrategyForPage = (strategy, data, page) => {
  const sid = String(strategy?.strategy_id || "");
  if (!sid.startsWith("intraday_momentum_")) return strategy;
  const universe = buildPageUniverse(data, page);
  if (!universe.size) return { ...strategy, items: [], history: [] };

  const items = (strategy?.items || []).filter((it) => isItemInPageUniverse(it, universe));
  const history = (strategy?.history || [])
    .map((h) => {
      if (!h || typeof h === "string") return h;
      const tickers = Array.isArray(h.tickers)
        ? h.tickers.filter((t) => universe.has(String(t).toUpperCase()) || universe.has(normalizeTicker(t)))
        : [];
      const base = { ...h, tickers };
      if (Array.isArray(h.tickers)) base.count = tickers.length;
      return base;
    })
    .filter((h) => {
      if (typeof h === "string") return true;
      if (!h) return false;
      const count = Number(h.count || 0);
      const tlen = Array.isArray(h.tickers) ? h.tickers.length : 0;
      return count > 0 || tlen > 0;
    });

  return { ...strategy, items, history };
};

const mergeIntradayPair = (strategies, { onPrefix, waitPrefix, mergedId, mergedTitle, market }) => {
  const on = strategies.find((s) => String(s?.strategy_id || "").startsWith(onPrefix));
  const wait = strategies.find((s) => String(s?.strategy_id || "").startsWith(waitPrefix));
  if (!on && !wait) return { merged: null, rest: strategies };

  const rest = strategies.filter(
    (s) => !String(s?.strategy_id || "").startsWith(onPrefix)
      && !String(s?.strategy_id || "").startsWith(waitPrefix)
  );

  const merged = {
    strategy_id: mergedId,
    title: mergedTitle,
    owner: on?.owner || wait?.owner || "HARSHIT",
    trade_type: "INTRADAY",
    market: market || on?.market || wait?.market || "all",
    notes: Array.from(
      new Set([
        ...(on?.notes || []),
        ...(wait?.notes || []),
        "Trade ON = VWAP retouch + green 5m close above VWAP.",
        "Trade WAIT = signal but confirmation pending.",
      ])
    ),
    items: [],
    history: [],
  };

  const addItems = (src, label) => {
    (src?.items || []).forEach((it) => {
      const lines = Array.isArray(it.lines) ? [...it.lines] : [];
      if (lines.length) {
        lines[0] = `Trade ${label}: ${lines[0]}`;
      } else {
        lines.push(`Trade ${label}`);
      }
      merged.items.push({ ...it, lines });
    });
  };
  addItems(on, "ON");
  addItems(wait, "WAIT");

  const historyMap = new Map();
  const addHistory = (src, label) => {
    (src?.history || []).forEach((h) => {
      const key = h.date || "NA";
      const curr = historyMap.get(key) || { date: key, on: 0, wait: 0 };
      if (label === "ON") curr.on += h.count || 0;
      if (label === "WAIT") curr.wait += h.count || 0;
      historyMap.set(key, curr);
    });
  };
  addHistory(on, "ON");
  addHistory(wait, "WAIT");
  merged.history = Array.from(historyMap.values())
    .sort((a, b) => `${a.date}`.localeCompare(`${b.date}`))
    .slice(-5)
    .map((h) => ({
      date: h.date,
      detail: `Trade ON ${h.on} / WAIT ${h.wait}`,
      count: (h.on || 0) + (h.wait || 0),
    }));

  return { merged, rest };
};

const buildSearchList = (items, datalistEl) => {
  datalistEl.innerHTML = items
    .map((item) => `<option value="${esc(item.label)}"></option>`)
    .join("");
};

const matchSearch = (items, query) => {
  const q = query.trim().toUpperCase();
  if (!q) return null;
  let match = items.find((item) => item.label.toUpperCase() === q);
  if (!match) {
    match = items.find((item) => item.label.toUpperCase().includes(q));
  }
  return match || null;
};

const renderTable = (headers, rows) => {
  if (!rows.length) return `<p class="muted">No data</p>`;
  return `
    <table>
      <thead>
        <tr>${headers.map((h) => `<th>${esc(h)}</th>`).join("")}</tr>
      </thead>
      <tbody>
        ${rows
          .map(
            (cells) => `
          <tr>
            ${cells.map((c) => `<td>${c}</td>`).join("")}
          </tr>
        `
          )
          .join("")}
      </tbody>
    </table>
  `;
};

const getSearchTargets = () => {
  if (PAGE === "all") {
    return { input: $("allSearchInput"), output: $("allSearchOutput") };
  }
  if (PAGE === "global") {
    return { input: $("globalSearchInput"), output: $("globalSearchOutput") };
  }
  if (PAGE === "commodities") {
    return { input: $("commoditySearchInput"), output: $("commoditySearchOutput") };
  }
  if (PAGE === "crypto") {
    return { input: $("cryptoSearchInput"), output: $("cryptoSearchOutput") };
  }
  return { input: $("indiaSearchInput"), output: $("indiaSearchOutput") };
};

const showAssetDetails = (key, value) => {
  const { input, output } = getSearchTargets();
  if (input) input.value = displayLabel(key);
  if (output) {
    output.innerHTML = `
      <h3>${esc(displayLabel(key))}</h3>
      ${renderAssetDetails(key, value)}
    `;
    output.scrollIntoView({ behavior: "smooth", block: "start" });
  }
};

const pctChange = (history, days) => {
  if (!Array.isArray(history) || history.length <= days) return null;
  const last = history[history.length - 1]?.close;
  const prev = history[history.length - 1 - days]?.close;
  if (typeof last !== "number" || typeof prev !== "number" || prev === 0) return null;
  return ((last / prev) - 1) * 100;
};

const formatChange = (pct) => {
  if (pct === null || pct === undefined) return "—";
  const cls = pct > 0 ? "up" : pct < 0 ? "down" : "neutral";
  return `<span class="${cls}">${pct.toFixed(2)}%</span>`;
};

const computeBreadth = (entries) => {
  if (!entries.length) return null;
  let up = 0;
  let down = 0;
  let side = 0;
  entries.forEach(([, v]) => {
    if (v?.trend === "PRIMARY_UPTREND") up += 1;
    else if (v?.trend === "PRIMARY_DOWNTREND") down += 1;
    else side += 1;
  });
  const total = up + down + side;
  return {
    up_pct: total ? (up / total) * 100 : 0,
    down_pct: total ? (down / total) * 100 : 0,
    sideways_pct: total ? (side / total) * 100 : 0
  };
};

const buildExecutiveSummary = (p) => {
  if (Array.isArray(p.executive_summary) && p.executive_summary.length) {
    return p.executive_summary.slice(0, 2);
  }

  const regime = p.regime?.regime || "UNKNOWN";
  const vol = p.regime?.volatility || "UNKNOWN";
  const dow = p.dow_confirmation || "UNKNOWN";
  const action = (p.action_guidance || [])[0] || "Stay selective";
  const up = fmtPct(p.breadth?.up_pct);
  const down = fmtPct(p.breadth?.down_pct);
  const riskTrend = p.risk_trend || "Risk neutral";

  return [
    `Regime: ${regime} (${vol}) | Dow: ${dow}`,
    `Breadth: ${up} up / ${down} down | ${riskTrend}. Action: ${action}`
  ];
};

const GLOBAL_INDICES = ["SP500", "NASDAQ", "DAX", "NIKKEI", "HANGSENG"];
const INDIA_INDICES = ["NIFTY", "BANKNIFTY", "SENSEX", "INDIA_VIX"];
const COMMODITIES = ["GOLD", "SILVER", "CRUDEOIL", "BRENT", "NATGAS", "COPPER", "PLATINUM"];
const CRYPTO = ["BTC", "ETH", "SOL", "BNB", "XRP"];
const PAGE = document.body?.dataset?.page || "india";
const FALLBACK_LIVE_API_URL = typeof window !== "undefined" && window.location
  ? `${window.location.origin}/live`
  : null;
const LIVE_API_URL = window?.LIVE_API_URL || "http://localhost:8765/live";
const THEMES = ["royal", "dark-royal", "sage", "beige", "sunrise", "slate", "silver"];
const DEFAULT_THEME = "royal";
const TURNSTILE_SITE_KEY = typeof window !== "undefined" ? String(window.TURNSTILE_SITE_KEY || "").trim() : "";
const TURNSTILE_ENABLED = typeof window !== "undefined" ? Boolean(window.TURNSTILE_ENABLED) : false;
const getSuggestUrl = () => {
  if (!LIVE_API_URL) return null;
  try {
    const u = new URL(LIVE_API_URL, window.location.href);
    if (u.pathname.endsWith("/live")) {
      u.pathname = u.pathname.replace(/\/live$/, "/suggest");
    } else {
      u.pathname = `${u.pathname.replace(/\/$/, "")}/suggest`;
    }
    u.search = "";
    return u.toString();
  } catch (err) {
    return null;
  }
};
const LIVE_SYMBOLS = Array.from(new Set([...INDIA_INDICES, ...GLOBAL_INDICES, ...COMMODITIES, ...CRYPTO]));
const IST_TIMEZONE = "Asia/Kolkata";
const ET_TIMEZONE = "America/New_York";
const AUTO_REFRESH_MS = typeof window !== "undefined" && window.AUTO_REFRESH_MS !== undefined
  ? Number(window.AUTO_REFRESH_MS)
  : 5000;
const CONTENT_READY_EVENT = "mp360:content-ready";
const CONTENT_EMPTY_EVENT = "mp360:content-empty";

const announcePublisherContentReady = (detail = {}) => {
  document.dispatchEvent(
    new CustomEvent(CONTENT_READY_EVENT, {
      detail: { contentRich: true, ...detail }
    })
  );
};

const announcePublisherContentEmpty = (reason = "no-content") => {
  document.dispatchEvent(
    new CustomEvent(CONTENT_EMPTY_EVENT, {
      detail: { contentRich: false, reason: String(reason || "no-content") }
    })
  );
};

const summarizeTrends = (entries) => {
  const up = [];
  const down = [];
  const side = [];
  entries.forEach(([name, value]) => {
    const trend = value?.trend;
    if (trend === "PRIMARY_UPTREND") up.push(displayLabel(name));
    else if (trend === "PRIMARY_DOWNTREND") down.push(displayLabel(name));
    else side.push(displayLabel(name));
  });
  return { up, down, side, total: entries.length };
};

const computeGainersLosers = (entries) => {
  let gainers = 0;
  let losers = 0;
  entries.forEach(([, value]) => {
    const history = value?.history;
    if (!Array.isArray(history) || history.length < 2) return;
    const latest = history[history.length - 1]?.close;
    const prev = history[history.length - 2]?.close;
    if (typeof latest !== "number" || typeof prev !== "number" || prev === 0) return;
    const change = (latest - prev) / prev;
    if (change > 0) gainers += 1;
    else if (change < 0) losers += 1;
  });
  return { gainers, losers };
};

const clampNum = (value, min = 0, max = 100, fallback = min) => {
  const num = Number(value);
  if (!Number.isFinite(num)) return fallback;
  return Math.min(max, Math.max(min, num));
};

const humanizeToken = (value, fallback = "—") => {
  if (value === null || value === undefined || value === "") return fallback;
  return String(value)
    .replace(/_/g, " ")
    .toLowerCase()
    .replace(/\b\w/g, (ch) => ch.toUpperCase());
};

const firstText = (...values) => {
  for (const value of values) {
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  return "";
};

const executiveToneFromStatus = ({ status = "", score = 0, breadthUp = null, riskTrend = "" } = {}) => {
  const statusUpper = String(status || "").toUpperCase();
  const riskUpper = String(riskTrend || "").toUpperCase();
  const bearish =
    statusUpper.includes("RISK-OFF")
    || score < 40
    || (typeof breadthUp === "number" && breadthUp < 40)
    || riskUpper.includes("UNCERTAIN")
    || riskUpper.includes("EXPANDING");
  const bullish =
    statusUpper.includes("RISK-ON")
    || (score >= 65 && typeof breadthUp === "number" && breadthUp >= 55 && !riskUpper.includes("UNCERTAIN"));
  if (bearish) return "bear";
  if (bullish) return "bull";
  return "neutral";
};

const executiveStanceCopy = (tone) => {
  if (tone === "bull") {
    return {
      chip: "Constructive Tape",
      headline: "Buy strength on leaders and stay with confirmed momentum.",
    };
  }
  if (tone === "bear") {
    return {
      chip: "Defensive Setup",
      headline: "Stay defensive and avoid weak rebound traps.",
    };
  }
  return {
    chip: "Selective Tape",
    headline: "Stay selective and focus only on confirmed leadership.",
  };
};

const executiveGlModel = (label, counts = {}, extraNote = "") => {
  const gainers = Number(counts?.gainers || 0);
  const losers = Number(counts?.losers || 0);
  const total = Number(counts?.total || (gainers + losers) || 0);
  const unchanged = Math.max(0, total - gainers - losers);
  const gainPct = total > 0 ? (gainers / total) * 100 : 0;
  const losePct = total > 0 ? (losers / total) * 100 : 0;

  let tone = "neutral";
  let badge = "Balanced";
  if (gainers > losers) {
    tone = "bull";
    badge = gainPct >= 65 ? "Strong Breadth" : "Buyers Ahead";
  } else if (losers > gainers) {
    tone = "bear";
    badge = losePct >= 65 ? "Heavy Selling" : "Sellers Ahead";
  }

  const participation = total > 0
    ? `${fmtNum(gainPct, 0)}% positive from ${total} tracked`
    : "Coverage unavailable";
  const note = firstText(
    extraNote,
    unchanged > 0 ? `${unchanged} unchanged names in the basket.` : "",
    participation
  );

  return { label, gainers, losers, total, tone, badge, gainPct, losePct, note };
};

const renderExecutiveGlCard = (model) => {
  if (!model) return "";
  return `
    <article class="exec-gl-card exec-tone-${esc(model.tone)}">
      <header class="exec-gl-head">
        <strong>${esc(model.label)}</strong>
        <span class="exec-gl-bias">${esc(model.badge)}</span>
      </header>
      <div class="exec-gl-ratio">
        <span class="win">${esc(fmtNum(model.gainers, 0))}</span>
        <span class="sep"> / </span>
        <span class="lose">${esc(fmtNum(model.losers, 0))}</span>
      </div>
      <div class="exec-gl-bar" aria-hidden="true">
        <span class="exec-gl-fill-up" style="width:${model.gainPct.toFixed(1)}%;"></span>
        <span class="exec-gl-fill-down" style="width:${model.losePct.toFixed(1)}%;"></span>
      </div>
      <div class="exec-gl-meta">
        <span>Gainers ${esc(fmtNum(model.gainers, 0))}</span>
        <span>Losers ${esc(fmtNum(model.losers, 0))}</span>
      </div>
      <p class="exec-gl-note">${esc(model.note)}</p>
    </article>
  `;
};

const renderExecutiveSignalRow = (label, text) => {
  if (!text) return "";
  return `
    <div class="exec-signal-row">
      <strong>${esc(label)}</strong>
      <p>${esc(text)}</p>
    </div>
  `;
};

const renderExecutiveWatchPill = (label, value) => {
  return `
    <div class="exec-watch-pill">
      <label>${esc(label)}</label>
      <strong>${esc(fmt(value))}</strong>
    </div>
  `;
};

const marketHealthBand = (score) => {
  const value = clampNum(score, 0, 100, 0);
  if (value <= 25) return "Fragile";
  if (value <= 50) return "Cautious";
  if (value <= 75) return "Stable";
  return "Strong";
};

const marketHealthVixState = (vix) => {
  if (typeof vix !== "number") return { label: "Volatility Unknown", tone: "neutral" };
  if (vix >= 25) return { label: "Stress High", tone: "bear" };
  if (vix >= 20) return { label: "Volatility High", tone: "bear" };
  if (vix >= 15) return { label: "Volatility Elevated", tone: "neutral" };
  return { label: "Volatility Contained", tone: "bull" };
};

const splitMarketHealthCopy = (text) => {
  const cleaned = firstText(text);
  if (!cleaned) return { main: "", secondary: "" };
  const parts = cleaned
    .split(".")
    .map((part) => part.trim())
    .filter(Boolean);
  return {
    main: parts[0] || cleaned,
    secondary: parts.slice(1).join(". "),
  };
};

const renderMarketHealthMetric = ({ label, value, detail = "", tone = "neutral", html = false }) => `
  <article class="market-health-stat mh-stat-${esc(tone)}">
    <span class="market-health-stat-label">${esc(label)}</span>
    <strong>${html ? value : esc(fmt(value))}</strong>
    ${detail ? `<p>${esc(detail)}</p>` : ""}
  </article>
`;

const renderMarketHealthIndexPill = ({ label, trend }) => {
  const tone = trend === "PRIMARY_UPTREND" ? "bull" : trend === "PRIMARY_DOWNTREND" ? "bear" : "neutral";
  const state = trend === "PRIMARY_UPTREND"
    ? "Uptrend"
    : trend === "PRIMARY_DOWNTREND"
      ? "Under Pressure"
      : "Range";
  return `
    <article class="market-health-index-pill mh-stat-${esc(tone)}">
      <span class="market-health-index-label">${esc(label)}</span>
      <strong>${esc(state)}</strong>
      <span class="market-health-index-dot mh-dot-${esc(tone)}" aria-hidden="true"></span>
    </article>
  `;
};

const renderMarketHealthMacroPill = (label, value) => {
  if (!value) return "";
  return `
    <div class="market-health-macro-pill">
      <label>${esc(label)}</label>
      <strong>${esc(fmt(value))}</strong>
    </div>
  `;
};

const renderIndiaMarketHealthCard = (data, p) => {
  const health = p.market_health?.india || {};
  const score = clampNum(health.score, 0, 100, 0);
  const vixValue = data?.INDIA_VIX?.current_price;
  const trackedIndices = [
    { label: "NIFTY 50", trend: data?.NIFTY?.trend },
    { label: "Bank NIFTY", trend: data?.BANKNIFTY?.trend },
    { label: "SENSEX", trend: data?.SENSEX?.trend },
  ];
  const uptrendCount = trackedIndices.filter((item) => item.trend === "PRIMARY_UPTREND").length;
  const downtrendCount = trackedIndices.filter((item) => item.trend === "PRIMARY_DOWNTREND").length;
  const rangeCount = trackedIndices.length - uptrendCount - downtrendCount;
  const breadthPct = trackedIndices.length ? (uptrendCount / trackedIndices.length) * 100 : null;
  const tone = executiveToneFromStatus({
    status: health.status,
    score,
    breadthUp: breadthPct,
    riskTrend: p.risk_trend,
  });
  const band = marketHealthBand(score);
  const vixState = marketHealthVixState(vixValue);
  const opportunity = splitMarketHealthCopy(health.opportunity);
  const keyNote = firstText(
    (health.notes || [])[0],
    (p.daily_intelligence?.brief || [])[0],
    "Market health is being monitored for breadth, volatility, and confirmation."
  );
  const summaryLine = [
    `${uptrendCount}/${trackedIndices.length} key indices in uptrend`,
    humanizeToken(p.dow_confirmation, "Dow status unknown"),
    vixState.label,
  ].filter(Boolean).join(" • ");
  const actionLabel = tone === "bear"
    ? "Reduce aggression"
    : tone === "bull"
      ? "Lean into leaders"
      : "Stay selective";
  const actionNote = tone === "bear"
    ? "Keep size smaller and prefer defensive setups until confirmation improves."
    : tone === "bull"
      ? "Momentum is healthier here, so let confirmed leadership do the work."
      : "Treat the tape as mixed and wait for clean confirmation before pressing trades.";
  const breadthNarrative = `${uptrendCount} in uptrend, ${downtrendCount} under pressure${rangeCount > 0 ? `, ${rangeCount} range-bound` : ""}.`;
  const playbookLead = opportunity.main || (tone === "bear" ? "Defensive setups" : tone === "bull" ? "Trend continuation" : "Selective setups");
  const playbookNote = opportunity.secondary || actionNote;
  const riskBadge = tone === "bear"
    ? "Capital protection first"
    : tone === "bull"
      ? "Leadership can be bought"
      : "Wait for cleaner confirmation";

  return `
    <section class="market-health-card mh-tone-${esc(tone)}">
      <div class="market-health-hero">
        <div class="market-health-copy">
          <div class="market-health-kicker">India Market Health</div>
          <div class="market-health-title-row">
            <h2>${esc(humanizeToken(health.status, "Unknown"))}</h2>
            <span class="market-health-chip">${esc(band)}</span>
          </div>
          <p class="market-health-summary">${esc(summaryLine)}</p>
          <div class="market-health-badge-row">
            <span class="market-health-badge">${esc(actionLabel)}</span>
            <span class="market-health-badge market-health-badge-soft">${esc(riskBadge)}</span>
          </div>
        </div>
        <div class="market-health-scorebox">
          <div class="market-health-score-ring" style="--score-pct:${score};">
            <div>
              <strong>${esc(fmtNum(score, 0))}</strong>
              <span>/ 100</span>
            </div>
          </div>
          <div class="market-health-score-copy">
            <strong>${esc(band)} Market Health</strong>
            <span>${esc(fmt(p.risk_trend))}</span>
          </div>
        </div>
      </div>

      <div class="market-health-index-strip">
        ${trackedIndices.map(renderMarketHealthIndexPill).join("")}
      </div>

      <div class="market-health-panel-grid">
        <article class="market-health-panel">
          <span class="market-health-panel-label">What Matters Today</span>
          <h3>${esc(keyNote)}</h3>
          <p>${esc(actionNote)}</p>
        </article>
        <article class="market-health-panel market-health-panel-accent">
          <span class="market-health-panel-label">Playbook</span>
          <h3>${esc(playbookLead)}</h3>
          <p>${esc(playbookNote)}</p>
        </article>
      </div>

      <div class="market-health-metric-row">
        ${renderMarketHealthMetric({
          label: "Trend Breadth",
          value: `${uptrendCount}/${trackedIndices.length}`,
          detail: breadthNarrative,
          tone: uptrendCount > downtrendCount ? "bull" : uptrendCount < downtrendCount ? "bear" : "neutral",
        })}
        ${renderMarketHealthMetric({
          label: "Dow Theory",
          value: humanizeToken(p.dow_confirmation, "Unknown"),
          detail: p.dow_confirmation === "CONFIRMED"
            ? "Index alignment is supporting the tape."
            : "Confirmation is missing, so leadership remains incomplete.",
          tone: p.dow_confirmation === "CONFIRMED" ? "bull" : "bear",
        })}
        ${renderMarketHealthMetric({
          label: "India VIX",
          value: `<span id="marketHealthVix">${esc(typeof vixValue === "number" ? fmtNum(vixValue, 2) : fmt(vixValue))}</span>`,
          detail: vixState.label,
          tone: vixState.tone,
          html: true,
        })}
      </div>

      <div class="market-health-macro">
        ${renderMarketHealthMacroPill("Rates", p.macro?.rates)}
        ${renderMarketHealthMacroPill("Inflation", p.macro?.inflation)}
        ${renderMarketHealthMacroPill("Liquidity", p.macro?.liquidity)}
      </div>
    </section>
  `;
};

const executiveToneFromBalance = (upCount = 0, downCount = 0, neutral = "neutral") => {
  const up = Number(upCount || 0);
  const down = Number(downCount || 0);
  if (up > down) return "bull";
  if (down > up) return "bear";
  return neutral;
};

const executiveTrendLabel = (trend) => {
  if (!trend) return "Watching";
  return humanizeToken(String(trend).replace(/^PRIMARY_/, ""));
};

const renderExecutiveInfoCard = (model) => {
  if (!model) return "";
  const metrics = Array.isArray(model.metrics) ? model.metrics : [];
  return `
    <article class="exec-info-card exec-tone-${esc(model.tone || "neutral")}">
      <header class="exec-info-head">
        <strong>${esc(model.label || "Overview")}</strong>
        ${model.badge ? `<span class="exec-info-chip">${esc(model.badge)}</span>` : ""}
      </header>
      <div class="exec-info-value">${esc(fmt(model.primary))}</div>
      ${model.secondary ? `<div class="exec-info-sub">${esc(model.secondary)}</div>` : ""}
      ${metrics.length ? `
        <div class="exec-info-metrics">
          ${metrics.map((metric) => `
            <div class="exec-info-metric">
              <label>${esc(metric.label)}</label>
              <span>${metric.html ? metric.value : esc(fmt(metric.value))}</span>
            </div>
          `).join("")}
        </div>
      ` : ""}
      ${model.note ? `<p class="exec-info-note">${esc(model.note)}</p>` : ""}
    </article>
  `;
};

const buildExecutiveAssetCard = (label, key, item, options = {}) => {
  const oneDay = pctChange(item?.history, 1);
  const oneWeek = pctChange(item?.history, 5);
  const trend = item?.trend;
  const tone = options.tone || executiveToneFromBalance(
    typeof oneDay === "number" && oneDay > 0 ? 1 : 0,
    typeof oneDay === "number" && oneDay < 0 ? 1 : 0,
    trend === "PRIMARY_UPTREND" ? "bull" : trend === "PRIMARY_DOWNTREND" ? "bear" : "neutral"
  );
  return {
    label,
    tone,
    badge: options.badge || executiveTrendLabel(trend),
    primary: formatPriceValue(item?.current_price, key, item, options.digits ?? 2),
    secondary: options.secondary || `${item?.price_source || "EOD"}${item?.price_timestamp ? ` · ${item.price_timestamp}` : ""}`,
    metrics: [
      { label: "1D", value: formatChange(oneDay), html: true },
      { label: "1W", value: formatChange(oneWeek), html: true },
    ],
    note: options.note || `${label} is currently tagged as ${executiveTrendLabel(trend).toLowerCase()}.`,
  };
};

const renderExecutiveSummaryShell = ({
  title = "Executive Summary",
  intro = "",
  tone = "neutral",
  chip = "",
  headline = "",
  note = "",
  score = 50,
  cardsHtml = "",
  footerItems = [],
  signalTitle = "What Matters Now",
  signalRowsHtml = "",
  watchTitle = "Radar",
  watchHtml = "",
} = {}) => `
  <section class="exec-summary-shell exec-tone-${esc(tone)}">
    <div class="exec-summary-main">
      <div class="exec-summary-topline">
        <div>
          <h2>${esc(title)}</h2>
          ${intro ? `<p>${esc(intro)}</p>` : ""}
        </div>
        ${chip ? `<span class="exec-summary-chip">${esc(chip)}</span>` : ""}
      </div>

      <div class="exec-stance-row">
        <div>
          <div class="exec-stance-label">Market Stance</div>
          <div class="exec-stance-main">${esc(headline)}</div>
          ${note ? `<div class="exec-stance-note">${esc(note)}</div>` : ""}
        </div>
        <div class="exec-score-ring" style="--score-pct:${clampNum(score, 0, 100, 0)};">
          <div>
            <strong>${esc(fmtNum(clampNum(score, 0, 100, 0), 0))}</strong>
            <span>Health Score</span>
          </div>
        </div>
      </div>

      <div class="exec-gl-board">
        ${cardsHtml}
      </div>

      ${footerItems.length ? `
        <div class="exec-summary-footer">
          ${footerItems.map((item) => `<span class="exec-summary-note">${esc(item)}</span>`).join("")}
        </div>
      ` : ""}
    </div>

    <aside class="exec-summary-side">
      <section class="exec-insight-card">
        <h3>${esc(signalTitle)}</h3>
        <div class="exec-signal-list">
          ${signalRowsHtml}
        </div>
      </section>

      <section class="exec-insight-card">
        <h3>${esc(watchTitle)}</h3>
        <div class="exec-watch-grid">
          ${watchHtml}
        </div>
      </section>
    </aside>
  </section>
`;

const renderIndiaExecutiveSummary = (data, p) => {
  const indiaHealth = p.market_health?.india || {};
  const breadth = p.breadth || {};
  const daily = p.daily_intelligence || {};
  const gl = p.gainers_losers || {};
  const vixValue = data?.INDIA_VIX?.current_price;
  const score = clampNum(indiaHealth.score, 0, 100, 0);
  const breadthUp = typeof breadth.up_pct === "number" ? breadth.up_pct : null;
  const breadthDown = typeof breadth.down_pct === "number" ? breadth.down_pct : null;
  const tone = executiveToneFromStatus({
    status: indiaHealth.status,
    score,
    breadthUp,
    riskTrend: p.risk_trend,
  });
  const stance = executiveStanceCopy(tone);
  const stanceNote = firstText(
    (indiaHealth.notes || []).join(" "),
    (daily.brief || []).join(" "),
    "Breadth, volatility, and leadership are being monitored for live opportunity quality."
  );
  const marketTone = firstText(
    (indiaHealth.notes || [])[0],
    (daily.brief || [])[0],
    `Risk trend: ${fmt(p.risk_trend)}.`
  );
  const participation = firstText(
    (daily.breadth_quality || [])[0],
    typeof breadthUp === "number" && typeof breadthDown === "number"
      ? `Breadth ${fmtPct(breadthUp)} up / ${fmtPct(breadthDown)} down.`
      : ""
  );
  const tacticalRead = firstText(
    indiaHealth.opportunity,
    (daily.risk_zones || [])[0],
    (daily.expected_behavior || [])[0],
    "Favor only clean setups with confirmation."
  );
  const macroLine = [p.macro?.rates, p.macro?.inflation, p.macro?.liquidity]
    .filter((value) => typeof value === "string" && value.trim())
    .join(" / ");
  const footerItems = [
    `Status: ${humanizeToken(indiaHealth.status, "Unknown")}`,
    typeof breadthUp === "number" ? `Breadth Up: ${fmtPct(breadthUp)}` : "",
    typeof breadthDown === "number" ? `Breadth Down: ${fmtPct(breadthDown)}` : "",
    macroLine ? `Macro: ${macroLine}` : "",
  ].filter(Boolean);

  const glCards = [
    executiveGlModel(
      "NIFTY 50",
      gl.india_nifty50,
      p.nifty50_trends
        ? `${p.nifty50_trends.bullish || 0} bullish / ${p.nifty50_trends.bearish || 0} bearish trend map.`
        : ""
    ),
    executiveGlModel(
      "Bank NIFTY",
      gl.india_banknifty,
      Number(gl.india_banknifty?.gainers || 0) > Number(gl.india_banknifty?.losers || 0)
        ? "Banks are helping hold the tape together."
        : "Banks are not providing broad support yet."
    ),
    executiveGlModel(
      "SENSEX",
      gl.india_sensex,
      Number(gl.india_sensex?.gainers || 0) > Number(gl.india_sensex?.losers || 0)
        ? "Large caps remain the cleaner leadership pocket."
        : "Heavyweights are still under pressure."
    ),
  ];

  return renderExecutiveSummaryShell({
    title: "Executive Summary",
    intro: "India market stance built from breadth, gainers/losers, event context, and smart-money state.",
    tone,
    chip: stance.chip,
    headline: stance.headline,
    note: stanceNote,
    score,
    cardsHtml: glCards.map(renderExecutiveGlCard).join(""),
    footerItems,
    signalRowsHtml: [
      renderExecutiveSignalRow("Market Tone", marketTone),
      renderExecutiveSignalRow("Participation", participation),
      renderExecutiveSignalRow("Tactical Read", tacticalRead),
    ].join(""),
    watchHtml: [
      renderExecutiveWatchPill("Risk Trend", p.risk_trend),
      renderExecutiveWatchPill(
        "Smart Money",
        `${humanizeToken(p.smart_money?.state, "Unknown")}${p.smart_money?.confidence ? ` / ${humanizeToken(p.smart_money.confidence)}` : ""}`
      ),
      renderExecutiveWatchPill("Event Trigger", humanizeToken(p.event_context?.trigger, "Unknown")),
      renderExecutiveWatchPill("India VIX", typeof vixValue === "number" ? fmtNum(vixValue, 2) : fmt(vixValue)),
    ].join(""),
  });
};

const renderGlobalExecutiveSummary = (data, p) => {
  const globalHealth = p.market_health?.global || {};
  const daily = p.daily_intelligence || {};
  const gl = p.gainers_losers || {};
  const detail = gl.global_indices_detail || {};
  const globalTrends = p.global_trends || {};
  const score = clampNum(
    globalHealth.score,
    0,
    100,
    globalTrends.total ? (Number(globalTrends.bullish || 0) / Number(globalTrends.total || 1)) * 100 : 50
  );
  const breadthUp = globalTrends.total ? (Number(globalTrends.bullish || 0) / Number(globalTrends.total || 1)) * 100 : null;
  const tone = executiveToneFromStatus({
    status: globalHealth.status,
    score,
    breadthUp,
    riskTrend: p.risk_trend,
  });
  const upIndices = summarizeTrends(GLOBAL_INDICES.filter((name) => data[name]).map((name) => [name, data[name]])).up;
  const cardsHtml = ["SP500", "NASDAQ", "DAX"]
    .map((name) => renderExecutiveGlCard(executiveGlModel(
      displayLabel(name),
      detail[name],
      detail[name]?.total
        ? `${detail[name]?.priced || 0}/${detail[name]?.total || 0} priced from mapped constituents.`
        : "Constituent breadth not available yet."
    )))
    .join("");
  const marketTone = firstText(
    (globalHealth.notes || [])[0],
    (daily.brief || [])[0],
    "Global indices remain the cleanest read on international risk appetite."
  );
  const participation = globalTrends.total
    ? `Mapped global universe: ${globalTrends.bullish || 0} bullish, ${globalTrends.bearish || 0} bearish, ${globalTrends.range || 0} range-bound.`
    : "Global trend participation will expand as the mapped universe grows.";
  const tacticalRead = firstText(
    (daily.attention || [])[0],
    (daily.risk_zones || [])[0],
    "Focus only on the indices and sectors holding relative strength."
  );
  return renderExecutiveSummaryShell({
    title: "Executive Summary",
    intro: "Global market stance built from index breadth, mapped stock participation, and current risk regime.",
    tone,
    chip: tone === "bull" ? "Global Risk-On" : tone === "bear" ? "Global Risk-Off" : "Global Selective",
    headline: tone === "bull"
      ? "Stay with global leaders and let relative strength guide entries."
      : tone === "bear"
        ? "Stay selective globally and avoid chasing weak rebounds."
        : "Pick only the markets showing clean leadership and steady breadth.",
    note: firstText(
      (globalHealth.notes || []).join(" "),
      (daily.brief || []).join(" "),
      "Global breadth is being monitored continuously through the mapped benchmark universe."
    ),
    score,
    cardsHtml,
    footerItems: [
      `Status: ${humanizeToken(globalHealth.status, "Unknown")}`,
      globalTrends.total ? `Bullish: ${globalTrends.bullish || 0}/${globalTrends.total}` : "",
      gl.global_overall?.total_assets ? `Mapped Stocks: ${gl.global_overall.total_assets}` : "",
      upIndices.length ? `Uptrend Indices: ${upIndices.join(", ")}` : "Uptrend Indices: None",
    ].filter(Boolean),
    signalRowsHtml: [
      renderExecutiveSignalRow("Market Tone", marketTone),
      renderExecutiveSignalRow("Participation", participation),
      renderExecutiveSignalRow("Tactical Read", tacticalRead),
    ].join(""),
    watchHtml: [
      renderExecutiveWatchPill("Risk Trend", p.risk_trend),
      renderExecutiveWatchPill("Event Trigger", humanizeToken(p.event_context?.trigger, "Unknown")),
      renderExecutiveWatchPill("Global Health", humanizeToken(globalHealth.status, "Unknown")),
      renderExecutiveWatchPill("Overall G/L", `${gl.global_overall?.gainers || 0} / ${gl.global_overall?.losers || 0}`),
    ].join(""),
  });
};

const renderCommoditiesExecutiveSummary = (data, p) => {
  const entries = COMMODITIES.filter((name) => data[name]).map((name) => [name, data[name]]);
  const summary = summarizeTrends(entries);
  const ct = p.commodity_trends || {};
  const bullish = Number(ct.bullish ?? summary.up.length);
  const bearish = Number(ct.bearish ?? summary.down.length);
  const total = Number(ct.total ?? entries.length ?? 0);
  const score = clampNum(total ? (bullish / total) * 100 : 50, 0, 100, 50);
  const tone = executiveToneFromBalance(bullish, bearish);
  const strongest = summary.up[0] || "No clear leader";
  const cardsHtml = ["GOLD", "SILVER", "CRUDEOIL"]
    .filter((key) => data[key])
    .map((key) => renderExecutiveInfoCard(buildExecutiveAssetCard(
      displayLabel(key),
      key,
      data[key],
      {
        note: key === "GOLD"
          ? "Gold is the anchor read for defensive commodity demand."
          : key === "SILVER"
            ? "Silver helps confirm whether metal strength is broadening."
            : "Crude helps frame global growth and inflation pressure."
      }
    )))
    .join("");
  return renderExecutiveSummaryShell({
    title: "Executive Summary",
    intro: "Commodities stance built from trend participation, spot prices, and relative leadership across the tracked basket.",
    tone,
    chip: tone === "bull" ? "Commodities Strong" : tone === "bear" ? "Commodities Soft" : "Commodities Mixed",
    headline: tone === "bull"
      ? "Follow the strongest commodity trends and let leadership do the work."
      : tone === "bear"
        ? "Stay selective in commodities and avoid forcing trend trades in weak tapes."
        : "Treat commodities as selective opportunities, not a one-way basket trade.",
    note: `${bullish} bullish, ${bearish} bearish, ${Number(ct.range ?? summary.side.length) || 0} range-bound across tracked commodities.`,
    score,
    cardsHtml,
    footerItems: [
      total ? `Tracked: ${total}` : "",
      `Uptrend: ${summary.up.join(", ") || "None"}`,
      `Downtrend: ${summary.down.join(", ") || "None"}`,
      `Event Trigger: ${humanizeToken(p.event_context?.trigger, "Unknown")}`,
    ].filter(Boolean),
    signalRowsHtml: [
      renderExecutiveSignalRow("Market Tone", `${bullish} bullish, ${bearish} bearish, ${Number(ct.range ?? summary.side.length) || 0} range-bound across the basket.`),
      renderExecutiveSignalRow("Participation", `Leadership pockets: ${summary.up.join(", ") || "None"}${summary.down.length ? `. Pressure pockets: ${summary.down.join(", ")}.` : "."}`),
      renderExecutiveSignalRow("Tactical Read", `Favor the cleanest continuation setups and let ${strongest} act as the lead signal where possible.`),
    ].join(""),
    watchHtml: [
      renderExecutiveWatchPill("Gold Trend", executiveTrendLabel(data?.GOLD?.trend)),
      renderExecutiveWatchPill("Silver Trend", executiveTrendLabel(data?.SILVER?.trend)),
      renderExecutiveWatchPill("Event Trigger", humanizeToken(p.event_context?.trigger, "Unknown")),
      renderExecutiveWatchPill("Best Pocket", strongest),
    ].join(""),
  });
};

const renderCryptoExecutiveSummary = (data, p) => {
  const entries = CRYPTO.filter((name) => data[name]).map((name) => [name, data[name]]);
  const summary = summarizeTrends(entries);
  const overall = p.gainers_losers?.crypto_overall || {};
  const gainers = Number(overall.gainers || 0);
  const losers = Number(overall.losers || 0);
  const total = Number(overall.total || entries.length || 0);
  const score = clampNum(total ? (gainers / total) * 100 : 50, 0, 100, 50);
  const tone = executiveToneFromBalance(gainers, losers);
  const cardsHtml = ["BTC", "ETH", "SOL"]
    .filter((key) => data[key])
    .map((key) => renderExecutiveInfoCard(buildExecutiveAssetCard(
      displayLabel(key),
      key,
      data[key],
      {
        note: key === "BTC"
          ? "BTC remains the main risk barometer for the crypto complex."
          : key === "ETH"
            ? "ETH helps confirm whether rotation is broad or narrow."
            : "SOL gives a fast read on higher-beta participation."
      }
    )))
    .join("");
  return renderExecutiveSummaryShell({
    title: "Executive Summary",
    intro: "Crypto stance built from live trend tags, 24x7 participation, and current breadth across the tracked coin set.",
    tone,
    chip: tone === "bull" ? "Crypto Bid" : tone === "bear" ? "Crypto Weak" : "Crypto Mixed",
    headline: tone === "bull"
      ? "Stay with the strongest crypto leaders and let BTC confirm risk appetite."
      : tone === "bear"
        ? "Respect downside pressure in crypto and avoid reacting to every bounce."
        : "Trade crypto selectively and use trend alignment as the filter.",
    note: `24h-style breadth snapshot: ${gainers} gainers, ${losers} losers, ${Number(overall.unchanged || 0)} unchanged in the tracked set.`,
    score,
    cardsHtml,
    footerItems: [
      total ? `Tracked: ${total}` : "",
      `Uptrend: ${summary.up.join(", ") || "None"}`,
      `Pressure: ${summary.down.join(", ") || "None"}`,
      "Market: 24x7",
    ].filter(Boolean),
    signalRowsHtml: [
      renderExecutiveSignalRow("Market Tone", `Crypto breadth is ${gainers > losers ? "supportive" : gainers < losers ? "under pressure" : "balanced"} with ${gainers} gainers versus ${losers} losers.`),
      renderExecutiveSignalRow("Participation", `Leadership currently shows up in ${summary.up.join(", ") || "very few names"} while weakness is concentrated in ${summary.down.join(", ") || "no major laggards"}.`),
      renderExecutiveSignalRow("Tactical Read", "Use BTC and ETH trend confirmation first, then look for clean continuation in higher-beta names."),
    ].join(""),
    watchHtml: [
      renderExecutiveWatchPill("BTC Trend", executiveTrendLabel(data?.BTC?.trend)),
      renderExecutiveWatchPill("ETH Trend", executiveTrendLabel(data?.ETH?.trend)),
      renderExecutiveWatchPill("24h G/L", `${gainers} / ${losers}`),
      renderExecutiveWatchPill("Event Trigger", humanizeToken(p.event_context?.trigger, "Unknown")),
    ].join(""),
  });
};

const renderPageExecutiveSummary = (data, p, page) => {
  if (page === "india") return renderIndiaExecutiveSummary(data, p);
  if (page === "global") return renderGlobalExecutiveSummary(data, p);
  if (page === "commodities") return renderCommoditiesExecutiveSummary(data, p);
  if (page === "crypto") return renderCryptoExecutiveSummary(data, p);
  return "";
};

const buildPageSummary = (data, p, page) => {
  if (page === "all") {
    const indiaEntries = INDIA_INDICES.filter((n) => data[n]).map((n) => [n, data[n]]);
    const globalEntries = GLOBAL_INDICES.filter((n) => data[n]).map((n) => [n, data[n]]);
    const commodityEntries = COMMODITIES.filter((n) => data[n]).map((n) => [n, data[n]]);
    const cryptoEntries = CRYPTO.filter((n) => data[n]).map((n) => [n, data[n]]);
    const allEntries = [...indiaEntries, ...globalEntries, ...commodityEntries, ...cryptoEntries];
    const summary = summarizeTrends(allEntries);
    const gl = p.gainers_losers || {};
    const indiaOverall = gl.india_overall || {};
    const globalOverall = gl.global_overall || {};
    const cryptoOverall = gl.crypto_overall || {};
    return [
      `Coverage: India ${indiaEntries.length}, Global ${globalEntries.length}, Commodities ${commodityEntries.length}, Crypto ${cryptoEntries.length}.`,
      `Trend map: ${summary.up.length} up, ${summary.down.length} down, ${summary.side.length} sideways.`,
      `India G/L: ${indiaOverall.gainers || 0}/${indiaOverall.losers || 0} | Global G/L: ${globalOverall.gainers || 0}/${globalOverall.losers || 0}.`,
      `Crypto G/L: ${cryptoOverall.gainers || 0}/${cryptoOverall.losers || 0} | Event trigger: ${fmt(p.event_context?.trigger)}.`,
    ];
  }
  if (page === "global") {
    const entries = GLOBAL_INDICES.filter((n) => data[n]).map((n) => [n, data[n]]);
    const summary = summarizeTrends(entries);
    const checked = entries.map(([n]) => displayLabel(n)).join(", ") || "N/A";
    const upList = summary.up.join(", ") || "None";
    const gl = p.gainers_losers || {};
    const idxCounts = gl.global_indices || {};
    const idxDetail = gl.global_indices_detail || {};
    const overallCounts = gl.global_overall || {};
    const gt = p.global_trends;
    const gtLine = gt
      ? `Global stocks: ${gt.bullish} bullish, ${gt.bearish} bearish, ${gt.range} range-bound.`
      : "";
    const d = p.daily_intelligence || {};
    const insight = (d.attention && d.attention.length ? d.attention[0] : (d.brief || [])[0]) || "";
    return [
      `Uptrend indices: ${upList}.`,
      `Indices checked: ${checked}.`,
      ...GLOBAL_INDICES.map((name) => {
        const d = idxDetail[name] || {};
        const total = d.total || 0;
        const priced = d.priced || 0;
        if (total === 0) {
          const hist = data[name]?.history || [];
          if (hist.length >= 2) {
            const prev = hist[hist.length - 2]?.close;
            const last = hist[hist.length - 1]?.close;
            if (typeof prev === "number" && typeof last === "number") {
              const proxy = last > prev ? "Up" : last < prev ? "Down" : "Flat";
              return `${displayLabel(name)} G/L (index proxy): ${proxy}.`;
            }
          }
          return `${displayLabel(name)} G/L: data unavailable.`;
        }
        if (priced === 0) {
          return `${displayLabel(name)} G/L: data unavailable (${priced}/${total} priced).`;
        }
        return `${displayLabel(name)} G/L: ${d.gainers || 0}/${d.losers || 0}.`;
      }),
      insight ? `Note: ${insight}` : null
    ];
  }
  if (page === "commodities") {
    const entries = COMMODITIES.filter((n) => data[n]).map((n) => [n, data[n]]);
    const summary = summarizeTrends(entries);
    const upList = summary.up.join(", ") || "None";
    const ct = p.commodity_trends;
    const ctLine = ct
      ? `Commodities: ${ct.bullish} bullish, ${ct.bearish} bearish, ${ct.range} range-bound.`
      : "";
    const d = p.daily_intelligence || {};
    const insight = (d.attention && d.attention.length ? d.attention[0] : (d.brief || [])[0]) || "";
    return [
      `Uptrend: ${upList}.`,
      `Commodities list: ${COMMODITIES.join(", ")}.`,
      `${ctLine || "Trend stats unavailable."}`,
      insight ? `Note: ${insight}` : null
    ];
  }
  if (page === "crypto") {
    const entries = CRYPTO.filter((n) => data[n]).map((n) => [n, data[n]]);
    const summary = summarizeTrends(entries);
    const upList = summary.up.join(", ") || "None";
    const gl = p.gainers_losers || {};
    const counts = gl.crypto_overall || {};
    const priced = counts.total || 0;
    const totalAssets = counts.total_assets || priced;
    const unchanged = counts.unchanged || 0;
    const d = p.daily_intelligence || {};
    const insight = (d.attention && d.attention.length ? d.attention[0] : (d.brief || [])[0]) || "";
    const glLine = totalAssets > 0 && priced === 0
      ? `G/L: data unavailable (${priced}/${totalAssets} priced).`
      : `G/L (24h): ${counts.gainers || 0}/${counts.losers || 0} (flat ${unchanged || 0}, priced ${priced}/${totalAssets || priced}).`;
    return [
      `Uptrend: ${upList}.`,
      `Crypto list: ${CRYPTO.join(", ")}.`,
      glLine,
      insight ? `Note: ${insight}` : null
    ];
  }
  const entries = INDIA_INDICES.filter((n) => data[n] && n !== "INDIA_VIX").map((n) => [n, data[n]]);
  const summary = summarizeTrends(entries);
  const checked = entries.map(([n]) => displayLabel(n)).join(", ") || "N/A";
  const upList = summary.up.join(", ") || "None";
  const gl = p.gainers_losers || {};
  const n50Counts = gl.india_nifty50 || {};
  const bankCounts = gl.india_banknifty || {};
  const sensexCounts = gl.india_sensex || {};
  const overallCounts = gl.india_overall || {};
  const n50 = p.nifty50_trends;
  const n50Line = n50
    ? `NIFTY 50: ${n50.bullish} bullish, ${n50.bearish} bearish, ${n50.range} range-bound.`
    : "";
  const d = p.daily_intelligence || {};
  const insight = (d.attention && d.attention.length ? d.attention[0] : (d.brief || [])[0]) || "";
  return [
    `Uptrend indices: ${upList}.`,
    `Indices checked: ${checked}.`,
    `NIFTY50 G/L: ${n50Counts.gainers || 0}/${n50Counts.losers || 0}.`,
    `BANKNIFTY G/L: ${bankCounts.gainers || 0}/${bankCounts.losers || 0}.`,
    `SENSEX G/L: ${sensexCounts.gainers || 0}/${sensexCounts.losers || 0}.`,
    insight ? `Note: ${insight}` : null
  ];
};

const ensureStatusRow = () => {
  const topbar = document.querySelector(".topbar");
  if (!topbar) return;
  let statusRow = document.querySelector(".status-row");
  if (!statusRow) {
    statusRow = document.createElement("div");
    statusRow.className = "status-row";
    statusRow.innerHTML = `
      <span id="liveStatusDot" class="status-dot offline"></span>
      <span id="liveStatusLabel" class="muted">Offline</span>
    `;
    const lastUpdated = $("lastUpdated");
    if (lastUpdated) {
      statusRow.appendChild(lastUpdated);
    }
    topbar.appendChild(statusRow);
  }
  const statusStack = document.querySelector(".status-stack");
  let marketRow = document.querySelector(".market-row");
  if (!marketRow) {
    marketRow = document.createElement("div");
    marketRow.className = "market-row";
    if (statusStack && statusRow.parentElement === statusStack) {
      statusStack.insertBefore(marketRow, statusRow.nextSibling);
    } else {
      statusRow.appendChild(marketRow);
    }
  }
  if (!$("marketStatusLabel")) {
    const marketLabel = document.createElement("span");
    marketLabel.id = "marketStatusLabel";
    marketLabel.className = "chip-offline";
    marketLabel.textContent = "Market Closed";
    marketRow.appendChild(marketLabel);
  } else {
    const existing = $("marketStatusLabel");
    if (existing && existing.parentElement !== marketRow) {
      marketRow.appendChild(existing);
    }
  }
  if (!$("marketCountdown")) {
    const marketCountdown = document.createElement("span");
    marketCountdown.id = "marketCountdown";
    marketCountdown.className = "chip-neutral";
    marketCountdown.textContent = "Opens in --:--:--";
    marketRow.appendChild(marketCountdown);
  } else {
    const existing = $("marketCountdown");
    if (existing && existing.parentElement !== marketRow) {
      marketRow.appendChild(existing);
    }
  }
};

const updateLiveIndicator = (data) => {
  const liveDot = $("liveStatusDot");
  const liveLabel = $("liveStatusLabel");
  if (!liveDot || !liveLabel) return;
  const isLive = Boolean(data?.__live_status?.ok);
  const isStale = Boolean(data?.__live_status?.stale);
  const dataFresh = lastDataUpdatedAt && Date.now() - lastDataUpdatedAt <= 120000;
  const showLive = isLive || dataFresh;
  liveDot.classList.toggle("live", showLive);
  liveDot.classList.toggle("offline", !showLive);
  if (showLive && isLive) {
    liveLabel.textContent = isStale ? "Live (stale)" : "Live";
  } else if (showLive && dataFresh) {
    liveLabel.textContent = "Live (data)";
  } else {
    liveLabel.textContent = "Offline";
  }
  liveLabel.classList.toggle("chip-live", showLive);
  liveLabel.classList.toggle("chip-offline", !showLive);
};

const getZonedNowUtc = (timeZone) => {
  const now = new Date();
  const fmt = new Intl.DateTimeFormat("en-GB", {
    timeZone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false
  });
  const parts = fmt.formatToParts(now);
  const map = {};
  parts.forEach((p) => {
    if (p.type !== "literal") map[p.type] = p.value;
  });
  const year = Number(map.year);
  const month = Number(map.month);
  const day = Number(map.day);
  const hour = Number(map.hour);
  const minute = Number(map.minute);
  const second = Number(map.second);
  return new Date(Date.UTC(year, month - 1, day, hour, minute, second));
};

const formatCountdown = (ms) => {
  if (ms <= 0 || !Number.isFinite(ms)) return "00:00:00";
  const totalSec = Math.floor(ms / 1000);
  const h = Math.floor(totalSec / 3600);
  const m = Math.floor((totalSec % 3600) / 60);
  const s = totalSec % 60;
  const pad = (n) => String(n).padStart(2, "0");
  return `${pad(h)}:${pad(m)}:${pad(s)}`;
};

const computeMarketSession = (cfg) => {
  if (cfg.alwaysLive) {
    return { status: "LIVE", label: "24/7 Live", target: null, detail: cfg.detail };
  }

  const now = getZonedNowUtc(cfg.timeZone);
  const day = now.getUTCDay();
  const openTime = new Date(Date.UTC(
    now.getUTCFullYear(),
    now.getUTCMonth(),
    now.getUTCDate(),
    cfg.openHour, cfg.openMinute, 0
  ));
  const closeTime = new Date(Date.UTC(
    now.getUTCFullYear(),
    now.getUTCMonth(),
    now.getUTCDate(),
    cfg.closeHour, cfg.closeMinute, 0
  ));
  const isWeekend = day === 0 || day === 6;

  if (isWeekend) {
    const daysToAdd = day === 6 ? 2 : 1;
    const nextOpen = new Date(Date.UTC(
      now.getUTCFullYear(),
      now.getUTCMonth(),
      now.getUTCDate() + daysToAdd,
      cfg.openHour, cfg.openMinute, 0
    ));
    return { status: "CLOSED", label: "Opens in", target: nextOpen, detail: `${cfg.label} closed for weekend` };
  }

  if (now < openTime) {
    return { status: "CLOSED", label: "Opens in", target: openTime, detail: `${cfg.label} opens at ${cfg.openText}` };
  }
  if (now >= openTime && now <= closeTime) {
    return { status: "LIVE", label: "Closes in", target: closeTime, detail: `${cfg.label} live (${cfg.openText}–${cfg.closeText})` };
  }

  const nextDay = day === 5 ? 3 : 1;
  const nextOpen = new Date(Date.UTC(
    now.getUTCFullYear(),
    now.getUTCMonth(),
    now.getUTCDate() + nextDay,
    cfg.openHour, cfg.openMinute, 0
  ));
  return { status: "CLOSED", label: "Opens in", target: nextOpen, detail: `${cfg.label} closed` };
};

const formatMarketHours = (cfg) => {
  if (!cfg || cfg.alwaysLive) return "";
  if (typeof cfg.openHour !== "number" || typeof cfg.openMinute !== "number") return "";
  const pad = (n) => String(n).padStart(2, "0");
  const open = `${pad(cfg.openHour)}:${pad(cfg.openMinute)}`;
  const close = `${pad(cfg.closeHour)}:${pad(cfg.closeMinute)}`;
  return `${open}–${close}`;
};

const updateMarketStatus = () => {
  const label = $("marketStatusLabel");
  const countdown = $("marketCountdown");
  if (!label || !countdown) return;
  const cfg = PAGE === "all"
    ? { alwaysLive: true, detail: "All Markets view (mixed sessions)" }
    : PAGE === "global"
    ? {
        label: "US Market",
        timeZone: ET_TIMEZONE,
        openHour: 9,
        openMinute: 30,
        closeHour: 16,
        closeMinute: 0,
        openText: "09:30 ET",
        closeText: "16:00 ET"
      }
    : PAGE === "commodities"
      ? {
          label: "Commodities (24x5)",
          timeZone: ET_TIMEZONE,
          openHour: 0,
          openMinute: 0,
          closeHour: 23,
          closeMinute: 59,
          openText: "00:00 ET",
          closeText: "23:59 ET"
        }
      : PAGE === "crypto"
        ? { alwaysLive: true, detail: "Crypto trades 24/7" }
        : {
            label: "NSE",
            timeZone: IST_TIMEZONE,
            openHour: 9,
            openMinute: 15,
            closeHour: 15,
            closeMinute: 30,
            openText: "09:15 IST",
            closeText: "15:30 IST"
          };
  const info = computeMarketSession(cfg);
  const hoursLabel = formatMarketHours(cfg);
  if (info.status === "LIVE") {
    label.textContent = hoursLabel ? `Market Open (${hoursLabel})` : "Market Open";
    label.classList.add("chip-live");
    label.classList.remove("chip-offline");
  } else {
    label.textContent = hoursLabel ? `Market Closed (${hoursLabel})` : "Market Closed • Rest mode";
    label.classList.add("chip-offline");
    label.classList.remove("chip-live");
  }
  if (info.target) {
    const remaining = info.target.getTime() - getZonedNowUtc(cfg.timeZone).getTime();
    countdown.textContent = `${info.label} ${formatCountdown(remaining)}${hoursLabel ? ` • Hours ${hoursLabel}` : ""}`;
  } else {
    countdown.textContent = info.detail || "Live";
  }
  countdown.classList.add("chip-neutral");
};

const formatTimeZone = (timeZone) => {
  const fmt = new Intl.DateTimeFormat("en-GB", {
    timeZone,
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false
  });
  return fmt.format(new Date());
};

const updateClocks = () => {
  const primary = $("clockPrimary");
  const secondary = $("clockSecondary");
  if (!primary || !secondary) return;
  if (PAGE === "all") {
    primary.textContent = `India: ${formatTimeZone(IST_TIMEZONE)} IST`;
    secondary.textContent = `US: ${formatTimeZone(ET_TIMEZONE)} ET`;
  } else if (PAGE === "india") {
    primary.textContent = `Current Time: ${formatTimeZone(IST_TIMEZONE)} IST`;
    secondary.textContent = "";
  } else if (PAGE === "global" || PAGE === "commodities") {
    primary.textContent = `Current Time: ${formatTimeZone(ET_TIMEZONE)} ET`;
    secondary.textContent = "";
  } else if (PAGE === "crypto") {
    primary.textContent = `Current Time: ${formatTimeZone(IST_TIMEZONE)} IST`;
    secondary.textContent = "";
  }
};

const fetchWithTimeout = async (url, options = {}, timeoutMs = 4000) => {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, { ...options, signal: controller.signal });
  } finally {
    clearTimeout(timer);
  }
};

let lastLiveOkAt = null;
let lastLiveOkTs = null;

const syncRealtimeKeySignals = (data) => {
  if (PAGE !== "india") return;
  const vixEl = $("keySignalVix");
  const vix = data?.INDIA_VIX?.current_price;
  const text = typeof vix === "number" ? fmtNum(vix, 2) : fmt(vix);
  if (vixEl) {
    vixEl.textContent = text;
  }
  const healthVixEl = $("marketHealthVix");
  if (healthVixEl) {
    healthVixEl.textContent = text;
  }
};

const syncActiveDetailPanels = (data) => {
  if (!data) return;

  if (PAGE === "india") {
    const input = $("indiaSearchInput");
    const output = $("indiaSearchOutput");
    if (input && output) {
      const query = input.value.trim();
      if (query) {
        const indiaIndexEntries = INDIA_INDICES.filter((name) => data[name]).map((name) => [name, data[name]]);
        const indiaStockEntries = Object.entries(data).filter(([, v]) => v?.type === "INDIA_STOCK");
        const searchItems = [...indiaIndexEntries, ...indiaStockEntries].map(([key, value]) => ({
          key,
          label: displayLabel(key),
          value
        }));
        const match = matchSearch(searchItems, query);
        if (match) {
          output.innerHTML = `
            <h3>${esc(displayLabel(match.key))}</h3>
            ${renderAssetDetails(match.key, match.value)}
          `;
        }
      }
    }
  } else if (PAGE === "all") {
    const input = $("allSearchInput");
    const output = $("allSearchOutput");
    if (input && output) {
      const query = input.value.trim();
      if (query) {
        const indiaIndexEntries = INDIA_INDICES.filter((name) => data[name]).map((name) => [name, data[name]]);
        const globalIndexEntries = GLOBAL_INDICES.filter((name) => data[name]).map((name) => [name, data[name]]);
        const commodityEntries = COMMODITIES.filter((name) => data[name]).map((name) => [name, data[name]]);
        const cryptoEntries = CRYPTO.filter((name) => data[name]).map((name) => [name, data[name]]);
        const indiaStockEntries = Object.entries(data).filter(([, v]) => v?.type === "INDIA_STOCK");
        const globalStockEntries = Object.entries(data).filter(([, v]) => v?.type === "GLOBAL_STOCK");
        const searchItems = [
          ...indiaIndexEntries,
          ...globalIndexEntries,
          ...commodityEntries,
          ...cryptoEntries,
          ...indiaStockEntries,
          ...globalStockEntries,
        ].map(([key, value]) => ({ key, label: displayLabel(key), value }));
        const match = matchSearch(searchItems, query);
        if (match) {
          output.innerHTML = `
            <h3>${esc(displayLabel(match.key))}</h3>
            ${renderAssetDetails(match.key, match.value)}
          `;
        }
      }
    }
  } else if (PAGE === "global") {
    const input = $("globalSearchInput");
    const output = $("globalSearchOutput");
    if (input && output) {
      const query = input.value.trim();
      if (query) {
        const globalIndexEntries = GLOBAL_INDICES.filter((name) => data[name]).map((name) => [name, data[name]]);
        const globalStockEntries = Object.entries(data).filter(([, v]) => v?.type === "GLOBAL_STOCK");
        const searchItems = [...globalIndexEntries, ...globalStockEntries].map(([key, value]) => ({
          key,
          label: displayLabel(key),
          value
        }));
        const match = matchSearch(searchItems, query);
        if (match) {
          output.innerHTML = `
            <h3>${esc(displayLabel(match.key))}</h3>
            ${renderAssetDetails(match.key, match.value)}
          `;
        }
      }
    }
    const globalStockSelect = $("globalStockSelect");
    const globalStockOutput = $("globalStockOutput");
    if (globalStockSelect && globalStockOutput && globalStockSelect.value) {
      const item = data[globalStockSelect.value];
      if (item) {
        globalStockOutput.innerHTML = renderAssetDetails(globalStockSelect.value, item);
      }
    }
  } else if (PAGE === "commodities") {
    const input = $("commoditySearchInput");
    const output = $("commoditySearchOutput");
    if (input && output) {
      const query = input.value.trim();
      if (query) {
        const commodityEntries = COMMODITIES.filter((name) => data[name]).map((name) => [name, data[name]]);
        const searchItems = commodityEntries.map(([key, value]) => ({
          key,
          label: displayLabel(key),
          value
        }));
        const match = matchSearch(searchItems, query);
        if (match) {
          output.innerHTML = `
            <h3>${esc(displayLabel(match.key))}</h3>
            ${renderAssetDetails(match.key, match.value)}
          `;
        }
      }
    }
  } else if (PAGE === "crypto") {
    const input = $("cryptoSearchInput");
    const output = $("cryptoSearchOutput");
    if (input && output) {
      const query = input.value.trim();
      if (query) {
        const cryptoEntries = CRYPTO.filter((name) => data[name]).map((name) => [name, data[name]]);
        const searchItems = cryptoEntries.map(([key, value]) => ({
          key,
          label: displayLabel(key),
          value
        }));
        const match = matchSearch(searchItems, query);
        if (match) {
          output.innerHTML = `
            <h3>${esc(displayLabel(match.key))}</h3>
            ${renderAssetDetails(match.key, match.value)}
          `;
        }
      }
    }
  }
};

const collectActiveLiveKeys = (data) => {
  const keys = new Set(LIVE_SYMBOLS);
  const addMatchedKey = (query, entries) => {
    if (!query || !entries.length) return;
    const searchItems = entries.map(([key, value]) => ({
      key,
      label: displayLabel(key),
      value
    }));
    const match = matchSearch(searchItems, query);
    if (match?.key) {
      keys.add(match.key);
    }
  };

  if (PAGE === "india") {
    const query = $("indiaSearchInput")?.value.trim();
    const indiaIndexEntries = INDIA_INDICES.filter((name) => data[name]).map((name) => [name, data[name]]);
    const indiaStockEntries = Object.entries(data).filter(([, v]) => v?.type === "INDIA_STOCK");
    addMatchedKey(query, [...indiaIndexEntries, ...indiaStockEntries]);
  } else if (PAGE === "global") {
    const query = $("globalSearchInput")?.value.trim();
    const globalIndexEntries = GLOBAL_INDICES.filter((name) => data[name]).map((name) => [name, data[name]]);
    const globalStockEntries = Object.entries(data).filter(([, v]) => v?.type === "GLOBAL_STOCK");
    addMatchedKey(query, [...globalIndexEntries, ...globalStockEntries]);
    const selected = $("globalStockSelect")?.value;
    if (selected && data[selected]) {
      keys.add(selected);
    }
  } else if (PAGE === "commodities") {
    const query = $("commoditySearchInput")?.value.trim();
    addMatchedKey(query, COMMODITIES.filter((name) => data[name]).map((name) => [name, data[name]]));
  } else if (PAGE === "crypto") {
    const query = $("cryptoSearchInput")?.value.trim();
    addMatchedKey(query, CRYPTO.filter((name) => data[name]).map((name) => [name, data[name]]));
  } else if (PAGE === "all") {
    const query = $("allSearchInput")?.value.trim();
    const indiaIndexEntries = INDIA_INDICES.filter((name) => data[name]).map((name) => [name, data[name]]);
    const globalIndexEntries = GLOBAL_INDICES.filter((name) => data[name]).map((name) => [name, data[name]]);
    const commodityEntries = COMMODITIES.filter((name) => data[name]).map((name) => [name, data[name]]);
    const cryptoEntries = CRYPTO.filter((name) => data[name]).map((name) => [name, data[name]]);
    const indiaStockEntries = Object.entries(data).filter(([, v]) => v?.type === "INDIA_STOCK");
    const globalStockEntries = Object.entries(data).filter(([, v]) => v?.type === "GLOBAL_STOCK");
    addMatchedKey(query, [
      ...indiaIndexEntries,
      ...globalIndexEntries,
      ...commodityEntries,
      ...cryptoEntries,
      ...indiaStockEntries,
      ...globalStockEntries,
    ]);
  }

  return Array.from(keys);
};

const applyLivePrices = async (data) => {
  if (!data || !LIVE_API_URL) return;
  const urls = [LIVE_API_URL];
  if (FALLBACK_LIVE_API_URL && FALLBACK_LIVE_API_URL !== LIVE_API_URL) {
    urls.push(FALLBACK_LIVE_API_URL);
  }
  const liveKeys = collectActiveLiveKeys(data);
  let lastError = null;
  for (const base of urls) {
    try {
      const url = `${base}?symbols=${encodeURIComponent(liveKeys.join(","))}`;
      const res = await fetchWithTimeout(url, { cache: "no-store" }, 4000);
      if (!res.ok) {
        lastError = new Error(`status_${res.status}`);
        continue;
      }
      const payload = await res.json();
      if (payload?.timestamp) {
        data.__live_status = { ok: true, checked_at: payload.timestamp };
        lastLiveOkAt = Date.now();
        lastLiveOkTs = payload.timestamp;
      }
      const prices = payload?.prices || {};
      Object.entries(prices).forEach(([key, val]) => {
        if (!data[key] || val?.price === null || val?.price === undefined) return;
        data[key] = {
          ...data[key],
          current_price: val.price,
          price_source: "LIVE",
          price_timestamp: val.timestamp || payload?.timestamp,
          day_range: val?.day_range || data[key]?.day_range,
        };
      });
      return;
    } catch (err) {
      lastError = err;
    }
  }
  const graceMs = 120000;
  if (lastLiveOkAt && Date.now() - lastLiveOkAt <= graceMs) {
    data.__live_status = {
      ok: true,
      checked_at: lastLiveOkTs,
      stale: true
    };
  } else {
    data.__live_status = { ok: false, error: lastError ? String(lastError) : "unknown" };
  }
};

const renderAssetDetails = (name, item) => {
  if (!item) {
    return `<p class="muted">Select an item to view details.</p>`;
  }

  const currentPrice = item.current_price;
  const currencyLabel = currencyLabelFor(name, item);
  const ranges = item.ranges
    ? renderTable(
        ["Horizon", "Low", "Median", "High", "Samples"],
        Object.entries(item.ranges).map(([h, r]) => [
          esc(h),
          esc(fmtPct(r.low_pct)),
          esc(fmtPct(r.median_pct)),
          esc(fmtPct(r.high_pct)),
          esc(fmtNum(r.samples, 0))
        ])
      )
    : `<p class="muted">Ranges not available.</p>`;

  const rangeLines = [];
  if (item.ranges && typeof currentPrice === "number") {
    const r3 = item.ranges["3M"];
    const r6 = item.ranges["6M"];
    const r12 = item.ranges["12M"];
    const formatRange = (label, range) => {
      if (!range) return null;
      const low = currentPrice * (1 + (range.low_pct || 0) / 100);
      const high = currentPrice * (1 + (range.high_pct || 0) / 100);
      return `${label}: ${formatPriceValue(low, name, item)} – ${formatPriceValue(high, name, item)}`;
    };
    const lines = [formatRange("3M range", r3), formatRange("6M range", r6), formatRange("12M range", r12)].filter(Boolean);
    rangeLines.push(...lines);
  }

  const sr = item.support_resistance;
  const srBlock = sr
    ? `
      <h4>Support / Resistance</h4>
      <div class="metric-row">
        ${renderMetric("Support (Near)", formatPriceValue(sr.support_near, name, item))}
        ${renderMetric("Resistance (Near)", formatPriceValue(sr.resistance_near, name, item))}
        ${renderMetric("Support (Major)", formatPriceValue(sr.support_major, name, item))}
        ${renderMetric("Resistance (Major)", formatPriceValue(sr.resistance_major, name, item))}
      </div>
      <p class="muted">Lookback ${sr.lookback_days} sessions, window ${sr.pivot_window}.</p>
    `
    : "";

  const ema = item.ema9 || {};
  const emaBlock = ema && (ema.ema9_signal || ema.ema9_daily || ema.ema9_weekly)
    ? `
      <h4>EMA9 Signal</h4>
      <div class="metric-row">
        ${renderMetric("Signal", ema.ema9_signal || "—")}
        ${renderMetric("Daily EMA9", fmtNum(ema.ema9_daily, 2))}
        ${renderMetric("Weekly EMA9", fmtNum(ema.ema9_weekly, 2))}
      </div>
      <p class="muted">Daily: ${esc(fmt(ema.ema9_daily_state))} | Weekly: ${esc(fmt(ema.ema9_weekly_state))}</p>
    `
    : "";

  const rr = item.risk_reward
    ? renderTable(
        ["Horizon", "Upside", "Downside", "RR", "Samples"],
        Object.entries(item.risk_reward).map(([h, r]) => [
          esc(h),
          esc(fmtPct(r.upside_pct)),
          esc(fmtPct(r.downside_pct)),
          esc(fmtNum(r.rr_ratio, 2)),
          esc(fmtNum(r.samples, 0))
        ])
      )
    : `<p class="muted">Risk–reward not available.</p>`;

  const historyRows = Array.isArray(item.history)
    ? item.history.slice(-6).map((h) => [
        esc(h.date),
        esc(formatPriceValue(h.close, name, item))
      ])
    : [];
  const history = historyRows.length
    ? renderTable(["Date", currencyLabel ? `Close (${currencyLabel})` : "Close"], historyRows)
    : `<p class="muted">History not available.</p>`;

  const priceMeta = item.price_timestamp
    ? `<p class="muted">Price time: ${esc(item.price_timestamp)} (${esc(item.price_source || "EOD")})</p>`
    : `<p class="muted">Price source: ${esc(item.price_source || "EOD")}</p>`;
  const dayRangeInline = renderDayRangeInline(name, item);

  return `
    <div class="metric-row">
      ${renderMetric("Trend", item.trend)}
      ${renderMetric("Price", formatPriceValue(item.current_price, name, item))}
      ${renderMetric("Freshness", item.freshness || "—")}
    </div>
    ${priceMeta}
    ${dayRangeInline}
    ${emaBlock}
    ${rangeLines.length ? `<h4>Where It Can Go</h4><p>${esc(rangeLines.join(" | "))}</p>` : ""}
    ${srBlock}
    <h4>Risk–Reward</h4>
    ${rr}
    <h4>Historical Ranges</h4>
    ${ranges}
    <h4>Recent Closes</h4>
    ${history}
  `;
};

const clampValue = (value, min, max) => Math.min(Math.max(value, min), max);

const computeRangePosition = (value, low, high) => {
  if (![value, low, high].every((n) => typeof n === "number") || high <= low) return 0;
  return clampValue(((value - low) / (high - low)) * 100, 0, 100);
};

const getRangePreviewDigits = (value) => (Math.abs(Number(value || 0)) >= 1000 ? 1 : 2);

const roundPreviewValue = (value, digits) => Number(Number(value).toFixed(digits));

const getAssetMarketLabel = (name, item) => {
  if (item?.type === "INDIA_STOCK") return "India Stock";
  if (item?.type === "GLOBAL_STOCK") return "Global Stock";
  if (item?.type === "COMMODITY") return "Commodity";
  if (item?.type === "CRYPTO") return "Crypto";
  if (INDIA_INDICES.includes(name)) return "India Index";
  if (GLOBAL_INDICES.includes(name)) return "Global Index";
  if (item?.type === "INDEX") return "Index";
  return "Market Asset";
};

const getRangeLabelAlign = (position) => {
  if (position < 16) return "align-left";
  if (position > 84) return "align-right";
  return "";
};

const getFirstFiniteNumber = (obj, keys) => {
  if (!obj || typeof obj !== "object") return null;
  for (const key of keys) {
    const value = Number(obj[key]);
    if (Number.isFinite(value)) return value;
  }
  return null;
};

const inferPreviewRangeVariant = (item) => {
  const trend = String(item?.trend || "").toLowerCase();
  if (/(bear|weak|sell|down|below|negative|distribution|risk-off)/.test(trend)) return "trend_down";
  if (/(recover|reclaim|bounce|mixed|neutral|reversal|volatile)/.test(trend)) return "recovery";
  return "trend_up";
};

const readStructuredDayRange = (item) => {
  const candidates = [
    item?.day_range,
    item?.session_range,
    item?.intraday,
    item?.ohlc,
    item?.price_action,
  ];

  for (const candidate of candidates) {
    const low = getFirstFiniteNumber(candidate, ["low", "day_low", "session_low", "low_price"]);
    const high = getFirstFiniteNumber(candidate, ["high", "day_high", "session_high", "high_price"]);
    const fallbackCurrent = Number(item?.current_price);
    const current = getFirstFiniteNumber(candidate, ["current", "last", "ltp", "price", "close"])
      ?? (Number.isFinite(fallbackCurrent) ? fallbackCurrent : null);
    const open = getFirstFiniteNumber(candidate, ["open", "day_open", "session_open", "open_price"]) ?? current;

    if ([low, high, open, current].every((value) => Number.isFinite(value)) && high > low) {
      const basis = String(candidate?.basis || candidate?.range_basis || candidate?.mode || "").toLowerCase();
      const sourceKind = basis === "daily" || basis === "eod" ? "daily_ohlc" : "live_ohlc";
      const sourceLabel = sourceKind === "daily_ohlc" ? "Last Daily Bar" : "Intraday OHLC";
      const description = sourceKind === "daily_ohlc"
        ? "Open, low, high, and close are from the latest completed daily bar."
        : "Open, low, high, and current are coming from the intraday day-range feed.";
      const digits = getRangePreviewDigits(current || high);
      const tone = current < open ? "red" : "green";
      return {
        low: roundPreviewValue(low, digits),
        high: roundPreviewValue(high, digits),
        open: roundPreviewValue(open, digits),
        current: roundPreviewValue(current, digits),
        tone,
        title: sourceKind === "daily_ohlc" ? "Daily Structure" : "Session Structure",
        description,
        source: sourceKind,
        sourceLabel,
      };
    }
  }

  return null;
};

const buildPreviewDayRange = (name, item, variant = "trend_up") => {
  const current = Number(item?.current_price);
  if (!Number.isFinite(current) || current <= 0) return null;
  const digits = getRangePreviewDigits(current);
  const trend = String(item?.trend || "");
  const tone = variant === "trend_down" ? "red" : "green";

  let low;
  let high;
  let open;
  let title;
  let description;

  if (variant === "trend_down") {
    high = current * 1.013;
    open = current * 1.007;
    low = current * 0.993;
    title = "Sell Pressure";
    description = "Open gave way early and current price is holding in the lower half of the day range.";
  } else if (variant === "recovery") {
    low = current * 0.984;
    open = current * 0.992;
    high = current * 1.009;
    title = "Recovery Structure";
    description = "Early weakness recovered well enough to regain the opening print and hold upper-range control.";
  } else {
    low = current * 0.988;
    open = current * 0.994;
    high = current * 1.008;
    title = "Opening Drive";
    description = "Open is holding and price is tracking closer to the day high with constructive session tone.";
  }

  low = roundPreviewValue(low, digits);
  high = roundPreviewValue(high, digits);
  open = roundPreviewValue(open, digits);

  return {
    name,
    item,
    title,
    description,
    tone,
    low,
    high,
    open,
    current: roundPreviewValue(current, digits),
    marketLabel: getAssetMarketLabel(name, item),
    trend,
    source: "derived_preview",
  };
};

const buildDisplayDayRange = (name, item, variant = null) => {
  if (!item) return null;
  const marketLabel = getAssetMarketLabel(name, item);
  const structured = readStructuredDayRange(item);
  if (structured) {
    return {
      name,
      item,
      marketLabel,
      trend: String(item?.trend || ""),
      ...structured,
    };
  }

  const fallback = buildPreviewDayRange(name, item, variant || inferPreviewRangeVariant(item));
  return fallback ? { ...fallback, marketLabel } : null;
};

const renderDayRangeInline = (name, item, variant = null) => {
  const sample = buildDisplayDayRange(name, item, variant);
  if (!sample) return "";

  const { low, high, open, current, tone, title, marketLabel, source, sourceLabel, description } = sample;
  const openPos = computeRangePosition(open, low, high);
  const currentPos = computeRangePosition(current, low, high);
  const fillLeft = Math.min(openPos, currentPos);
  const fillWidth = Math.max(Math.abs(currentPos - openPos), 1.2);
  const currentVsOpen = open ? ((current / open) - 1) * 100 : null;
  const rangePosition = computeRangePosition(current, low, high);
  const toHighPct = high ? ((current / high) - 1) * 100 : null;
  const sourceTone = source === "derived_preview" ? tone : "neutral";
  const chipLabel = sourceLabel || (source === "daily_ohlc" ? "Last Daily Bar" : source === "live_ohlc" ? "Intraday OHLC" : "Derived Preview");
  const sourceCopy = description || (source === "derived_preview"
    ? "Open, low, and high are derived locally until backend intraday OHLC is connected."
    : "Open, low, high, and current are flowing from structured market data.");
  const currentLabel = source === "daily_ohlc" ? "Close" : "Current";
  const digits = getRangePreviewDigits(current);

  return `
    <div class="day-range-inline">
      <h4>Price Action Range</h4>
      <div class="day-range-head">
        <strong>${esc(title || "Session Structure")}</strong>
        <span class="day-range-chip ${sourceTone}">${esc(chipLabel)}</span>
      </div>
      <div class="day-range-asset">
        <span class="day-range-symbol">${esc(displayLabel(name))}</span>
        <span class="day-range-market">${esc(marketLabel)}</span>
      </div>
      <div class="day-range-stage">
        <div class="day-range-rail">
          <div class="day-range-fill ${tone}" style="left:${fillLeft}%; width:${fillWidth}%;"></div>
          <div class="day-range-marker open" style="left:${openPos}%;">
            <div class="day-range-marker-label ${getRangeLabelAlign(openPos)}">O ${esc(formatPriceValue(open, name, item, digits))}</div>
            <div class="dot"></div>
          </div>
          <div class="day-range-marker current ${tone}" style="left:${currentPos}%;">
            <div class="day-range-marker-label ${getRangeLabelAlign(currentPos)}">C ${esc(formatPriceValue(current, name, item, digits))}</div>
            <div class="dot"></div>
          </div>
        </div>
        <div class="day-range-ends">
          <div class="day-range-end">
            <span>Low</span>
            <b>${esc(formatPriceValue(low, name, item, digits))}</b>
          </div>
          <div class="day-range-end">
            <span>High</span>
            <b>${esc(formatPriceValue(high, name, item, digits))}</b>
          </div>
        </div>
      </div>
      <div class="metric-row">
        ${renderMetric("Vs Open", currentVsOpen !== null ? `${currentVsOpen >= 0 ? "+" : ""}${currentVsOpen.toFixed(2)}%` : "—")}
        ${renderMetric("In Range", `${fmtNum(rangePosition, 0)}%`)}
        ${renderMetric("To High", toHighPct !== null ? `${toHighPct.toFixed(2)}%` : "—")}
      </div>
      <p class="muted">Open: ${esc(formatPriceValue(open, name, item, digits))} | Low: ${esc(formatPriceValue(low, name, item, digits))} | High: ${esc(formatPriceValue(high, name, item, digits))} | ${esc(currentLabel)}: ${esc(formatPriceValue(current, name, item, digits))}</p>
      <p class="muted">${esc(sourceCopy)}</p>
    </div>
  `;
};

const renderPreviewDayRangeCard = (sample) => {
  if (!sample) return "";
  const { name, item, low, high, open, current, tone, title, description, marketLabel } = sample;
  const openPos = computeRangePosition(open, low, high);
  const currentPos = computeRangePosition(current, low, high);
  const fillLeft = Math.min(openPos, currentPos);
  const fillWidth = Math.max(Math.abs(currentPos - openPos), 1.2);
  const currentVsOpen = open ? ((current / open) - 1) * 100 : null;
  const dayRangePct = low ? ((high / low) - 1) * 100 : null;
  const fromLowPct = low ? ((current / low) - 1) * 100 : null;
  const toHighPct = high ? ((current / high) - 1) * 100 : null;
  const rangePosition = computeRangePosition(current, low, high);
  const toneLabel = tone === "red" ? "Red Session" : "Green Session";
  const changeClass = currentVsOpen !== null && currentVsOpen < 0 ? "down" : "up";
  const openAlign = getRangeLabelAlign(openPos);
  const currentAlign = getRangeLabelAlign(currentPos);

  return `
    <div class="day-range-card">
      <div class="day-range-head">
        <strong>${esc(title)}</strong>
        <span class="day-range-chip ${tone}">${esc(toneLabel)}</span>
      </div>
      <div class="day-range-asset">
        <span class="day-range-symbol">${esc(displayLabel(name))}</span>
        <span class="day-range-market">${esc(marketLabel)}</span>
      </div>
      <p class="day-range-trend-note">${esc(description)}</p>
      <div class="day-range-stage">
        <div class="day-range-rail">
          <div class="day-range-fill ${tone}" style="left:${fillLeft}%; width:${fillWidth}%;"></div>
          <div class="day-range-marker open" style="left:${openPos}%;">
            <div class="day-range-marker-label ${openAlign}">O ${esc(formatPriceValue(open, name, item, getRangePreviewDigits(open)))}</div>
            <div class="dot"></div>
          </div>
          <div class="day-range-marker current ${tone}" style="left:${currentPos}%;">
            <div class="day-range-marker-label ${currentAlign}">C ${esc(formatPriceValue(current, name, item, getRangePreviewDigits(current)))}</div>
            <div class="dot"></div>
          </div>
        </div>
        <div class="day-range-ends">
          <div class="day-range-end">
            <span>Low</span>
            <b>${esc(formatPriceValue(low, name, item, getRangePreviewDigits(low)))}</b>
          </div>
          <div class="day-range-end">
            <span>High</span>
            <b>${esc(formatPriceValue(high, name, item, getRangePreviewDigits(high)))}</b>
          </div>
        </div>
      </div>
      <div class="day-range-stats">
        <div class="day-range-stat">
          <label>Vs Open</label>
          <div class="value ${changeClass}">${currentVsOpen !== null ? `${currentVsOpen >= 0 ? "+" : ""}${currentVsOpen.toFixed(2)}%` : "—"}</div>
          <div class="sub">${tone === "red" ? "sellers ahead" : "buyers ahead"}</div>
        </div>
        <div class="day-range-stat">
          <label>Day Span</label>
          <div class="value">${dayRangePct !== null ? `${dayRangePct.toFixed(2)}%` : "—"}</div>
          <div class="sub">high vs low</div>
        </div>
        <div class="day-range-stat">
          <label>In Range</label>
          <div class="value">${fmtNum(rangePosition, 0)}%</div>
          <div class="sub">current inside day</div>
        </div>
        <div class="day-range-stat">
          <label>To High</label>
          <div class="value">${toHighPct !== null ? `${toHighPct.toFixed(2)}%` : "—"}</div>
          <div class="sub">below high</div>
        </div>
      </div>
      <div class="day-range-meta">
        <span class="day-range-pill">From Low: ${fromLowPct !== null ? `+${fromLowPct.toFixed(2)}%` : "—"}</span>
        <span class="day-range-pill">Trend: ${esc(fmt(item?.trend))}</span>
      </div>
    </div>
  `;
};

const renderIndiaRangePreview = (data) => {
  const root = $("indiaRangePreview");
  if (!root || PAGE !== "india") return;
  const samples = [
    buildPreviewDayRange("NIFTY", data?.NIFTY, "trend_up"),
    buildPreviewDayRange("BANKNIFTY", data?.BANKNIFTY, "trend_down"),
    buildPreviewDayRange("SENSEX", data?.SENSEX, "trend_up"),
    buildPreviewDayRange("RELIANCE", data?.RELIANCE, "recovery"),
  ].filter(Boolean);

  if (!samples.length) {
    root.innerHTML = "";
    return;
  }

  root.innerHTML = `
    <div class="card">
      <div class="day-range-section-head">
        <div>
          <h2>India Price Action Range</h2>
          <p>Current price comes from the live/EOD India page feed. Open, high, and low are visually mocked for now and will switch to real intraday OHLC as soon as we connect the backend day-range feed.</p>
        </div>
        <span class="day-range-section-chip">Local Intraday Sample</span>
      </div>
      <div class="day-range-grid">
        ${samples.map(renderPreviewDayRangeCard).join("")}
      </div>
    </div>
  `;
};

const renderRegimeCard = (p) => {
  const m = p.market_regime;
  const r = p.regime;
  return `
    <div class="card">
      <h3>Market Regime</h3>
      ${m ? `
        <p><b>Regime:</b> ${esc(fmt(m.regime))}</p>
        <p><b>Trend:</b> ${esc(fmt(m.trend))}</p>
        <p><b>Volatility:</b> ${esc(fmt(m.volatility_state))}</p>
        <p><b>Bias:</b> ${esc(fmt(m.bias))}</p>
      ` : ""}
      ${r ? `
        <div class="section-divider"></div>
        <p><b>Risk Regime:</b> ${esc(fmt(r.regime))}</p>
        <p><b>Volatility:</b> ${esc(fmt(r.volatility))}</p>
        <p><b>Confidence:</b> ${esc(fmt(r.confidence))}</p>
      ` : ""}
    </div>
  `;
};

let refreshInFlight = false;

let lastGeneratedAt = null;
let lastDataUpdatedAt = null;
const applyTheme = (theme) => {
  const body = document.body;
  const root = document.documentElement;
  if (!body || !root) return;
  THEMES.forEach((t) => {
    body.classList.remove(`theme-${t}`);
    root.classList.remove(`theme-${t}`);
  });
  body.classList.add(`theme-${theme}`);
  root.classList.add(`theme-${theme}`);
  try {
    localStorage.setItem("mc_theme", theme);
  } catch (err) {
    // ignore
  }
};

const initTheme = () => {
  let theme = DEFAULT_THEME;
  try {
    const saved = localStorage.getItem("mc_theme");
    const normalized = saved === "pista" ? "sage" : saved;
    if (normalized && THEMES.includes(normalized)) theme = normalized;
  } catch (err) {
    // ignore
  }
  applyTheme(theme);
  const select = $("themeSelect");
  if (select) {
    select.value = theme;
    select.addEventListener("change", () => {
      const next = select.value;
      if (THEMES.includes(next)) {
        applyTheme(next);
      }
    });
  }
};
const MONTHS = {
  Jan: 0, Feb: 1, Mar: 2, Apr: 3, May: 4, Jun: 5,
  Jul: 6, Aug: 7, Sep: 8, Oct: 9, Nov: 10, Dec: 11
};

const parseGeneratedAt = (value) => {
  if (!value) return null;
  const iso = Date.parse(value);
  if (!Number.isNaN(iso)) return iso;
  const m = String(value).match(/^(\d{2})\s+([A-Za-z]{3})\s+(\d{4}),\s+(\d{1,2}):(\d{2})(?::(\d{2}))?\s+(AM|PM)\s+IST$/);
  if (!m) return null;
  const day = Number(m[1]);
  const month = MONTHS[m[2]];
  const year = Number(m[3]);
  let hour = Number(m[4]);
  const minute = Number(m[5]);
  const second = Number(m[6] || 0);
  const ampm = m[7];
  if (ampm === "PM" && hour < 12) hour += 12;
  if (ampm === "AM" && hour === 12) hour = 0;
  if (month === undefined) return null;
  const utcMs = Date.UTC(year, month, day, hour - 5, minute - 30, second);
  return utcMs;
};

const formatDateTimeInZone = (ms, timeZone, label) => {
  if (!ms) return "";
  const fmt = new Intl.DateTimeFormat("en-GB", {
    timeZone,
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: true
  });
  const parts = fmt.formatToParts(new Date(ms));
  const map = {};
  parts.forEach((p) => {
    if (p.type !== "literal") map[p.type] = p.value;
  });
  const day = map.day || "";
  const month = map.month || "";
  const year = map.year || "";
  const hour = map.hour || "";
  const minute = map.minute || "";
  const second = map.second || "";
  const dayPeriod = (map.dayPeriod || "").toUpperCase();
  const tz = label ? ` ${label}` : "";
  return `${day} ${month} ${year}, ${hour}:${minute}:${second} ${dayPeriod}${tz}`.trim();
};

const bindSuggestionForm = () => {
  const form = $("suggestionForm");
  if (!form || form.dataset.bound) return;
  form.dataset.bound = "true";
  const name = $("suggestionName");
  const email = $("suggestionEmail");
  const honeypot = $("suggestionCompany");
  const text = $("suggestionText");
  const status = $("suggestionStatus");
  const turnstileWrap = $("turnstileWrap");
  let turnstileWidgetId = null;
  const maybeInitTurnstile = () => {
    if (!turnstileWrap) return;
    if (!TURNSTILE_ENABLED || !TURNSTILE_SITE_KEY) return;
    if (typeof window.turnstile === "undefined") {
      const tag = document.createElement("script");
      tag.src = "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit";
      tag.async = true;
      tag.defer = true;
      document.head.appendChild(tag);
    }
    turnstileWrap.hidden = false;
    const tryRender = () => {
      if (turnstileWidgetId !== null) return;
      if (typeof window.turnstile === "undefined") return;
      turnstileWidgetId = window.turnstile.render(turnstileWrap, { sitekey: TURNSTILE_SITE_KEY });
    };
    tryRender();
    setTimeout(tryRender, 300);
    setTimeout(tryRender, 1200);
  };
  maybeInitTurnstile();
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!text || !status) return;
    const nameValue = name?.value.trim() || "";
    const emailValue = email?.value.trim() || "";
    const suggestion = text.value.trim();
    const hpValue = honeypot?.value.trim() || "";
    if (!nameValue || nameValue.length > 100) {
      status.textContent = "Please enter your name.";
      return;
    }
    if (!emailValue || !emailValue.includes("@")) {
      status.textContent = "Please enter a valid email.";
      return;
    }
    if (!suggestion) {
      status.textContent = "Please add a short suggestion.";
      return;
    }
    if (hpValue) {
      status.textContent = "Could not send. Please try again.";
      return;
    }
    const endpoint = getSuggestUrl();
    if (!endpoint) {
      status.textContent = "Suggestion service not configured.";
      return;
    }
    status.textContent = "Sending...";
    try {
      const turnstileToken =
        TURNSTILE_ENABLED && typeof window.turnstile !== "undefined" && turnstileWidgetId !== null
          ? window.turnstile.getResponse(turnstileWidgetId)
          : "";
      const payload = {
        name: nameValue,
        email: emailValue,
        message: suggestion,
        company: hpValue,
        turnstile_token: turnstileToken,
        page: PAGE
      };
      const res = await fetchWithTimeout(
        endpoint,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload)
        },
        4000
      );
      if (!res.ok) {
        status.textContent = "Could not send. Please try again.";
        if (TURNSTILE_ENABLED && typeof window.turnstile !== "undefined" && turnstileWidgetId !== null) {
          try { window.turnstile.reset(turnstileWidgetId); } catch (_) {}
        }
        return;
      }
      status.textContent = "Thanks! Your suggestion was sent.";
      text.value = "";
      if (name) name.value = "";
      if (honeypot) honeypot.value = "";
      if (TURNSTILE_ENABLED && typeof window.turnstile !== "undefined" && turnstileWidgetId !== null) {
        try { window.turnstile.reset(turnstileWidgetId); } catch (_) {}
      }
    } catch (err) {
      status.textContent = "Network error. Please try again.";
    }
  });
};

async function refreshData() {
  if (refreshInFlight) return;
  refreshInFlight = true;
  try {
    ensureStatusRow();
    updateMarketStatus();
    const res = await fetch(`data.json?ts=${Date.now()}`, { cache: "no-store" });
    if (!res.ok) {
      throw new Error(`data_fetch_status_${res.status}`);
    }
    const p = await res.json();
    const data = p.data || {};
    const render = () => {
      let strategyCardCount = 0;
      let strategySignalCount = 0;
      let eventCount = 0;
      let newsCount = 0;
      const root = $("liveIntelligence");

      const lastUpdated = $("lastUpdated");
      if (lastUpdated) {
        const parsed = parseGeneratedAt(p.generated_at);
        const lastUpdatedZone =
          PAGE === "global" || PAGE === "commodities"
            ? { tz: ET_TIMEZONE, label: "ET" }
            : { tz: IST_TIMEZONE, label: "IST" };
        lastUpdated.textContent = parsed
          ? `Last Updated: ${formatDateTimeInZone(parsed, lastUpdatedZone.tz, lastUpdatedZone.label)}`
          : `Last Updated: ${fmt(p.generated_at)}`;
      }
      if (p.generated_at) {
        const parsed = parseGeneratedAt(p.generated_at);
        if (parsed) lastDataUpdatedAt = parsed;
      }
      const liveTime = $("livePriceTime");
      if (liveTime) {
        const liveTs = data.__live_status?.checked_at;
        if (liveTs) {
          liveTime.textContent = `Live Price Time: ${liveTs}`;
          liveTime.style.display = "inline";
        } else {
          liveTime.textContent = "";
          liveTime.style.display = "none";
        }
      }

      updateLiveIndicator(data);

      const indiaStockEntries = Object.entries(data).filter(([, v]) => v?.type === "INDIA_STOCK");
      const globalStockEntries = Object.entries(data).filter(([, v]) => v?.type === "GLOBAL_STOCK");
      const cryptoEntries = Object.entries(data).filter(([, v]) => v?.type === "CRYPTO");
      const breadthEntries = Object.entries(data).filter(([, v]) => v?.type === "STOCK_BREADTH");

      const breadthStocks = computeBreadth(indiaStockEntries);
      const breadthBroader = computeBreadth(breadthEntries);
      const executiveSummary = buildPageSummary(data, p, PAGE);
      const executiveSummaryHtml = renderPageExecutiveSummary(data, p, PAGE);
      const vixValue = data?.INDIA_VIX?.current_price;

      if (root) {
        const summaryLines = executiveSummary.filter(Boolean);
        root.innerHTML = `
          ${executiveSummaryHtml
            ? executiveSummaryHtml
            : `
              <section class="grid-2">
                <div class="card">
                  <h3>Executive Summary</h3>
                  ${summaryLines.length ? renderList(summaryLines, "Summary unavailable") : "<p class=\"muted\">Summary unavailable</p>"}
                </div>
                <div class="card">
                  <h3>Key Signals</h3>
                  <div class="metric-row">
          ${PAGE === "all"
            ? renderMetric("Markets", "India + Global + Commodities + Crypto")
            : PAGE === "global"
            ? renderMetric("Global Health", p.market_health?.global?.status)
            : PAGE === "commodities"
              ? renderMetric("Gold Trend", data?.GOLD?.trend)
              : PAGE === "crypto"
                ? renderMetric("BTC Trend", data?.BTC?.trend)
                : renderMetric("Risk Trend", p.risk_trend)}
                    ${PAGE === "all"
                      ? renderMetric("Risk Trend", p.risk_trend)
                      : PAGE === "global"
                      ? renderMetric("Risk Trend", p.risk_trend)
                      : PAGE === "commodities"
                        ? renderMetric("Silver Trend", data?.SILVER?.trend)
                        : PAGE === "crypto"
                          ? renderMetric("ETH Trend", data?.ETH?.trend)
                          : renderMetric("Smart Money", p.smart_money?.state)}
                    ${PAGE === "all"
                      ? renderMetric("Event Trigger", p.event_context?.trigger)
                      : PAGE === "global"
                      ? renderMetric("Event Trigger", p.event_context?.trigger)
                      : PAGE === "commodities"
                        ? renderMetric("Event Trigger", p.event_context?.trigger)
                        : PAGE === "crypto"
                          ? renderMetric("Event Trigger", p.event_context?.trigger)
                          : renderMetric("Event Trigger", p.event_context?.trigger)}
                    ${PAGE === "india" ? renderMetric("India VIX", fmtNum(vixValue, 2), "keySignalVix") : ""}
                  </div>
                  ${PAGE === "india"
                    ? `<p class="muted">Breadth tracked: ${fmtPct(p.breadth?.up_pct)} up / ${fmtPct(p.breadth?.down_pct)} down.</p>`
                    : PAGE === "all"
                      ? `<p class="muted">Unified cross-market snapshot with a single strategy desk.</p>`
                      : `<p class="muted">Key signals tailored to ${PAGE} data.</p>`
                  }
                </div>
              </section>
            `}
        `;
        root.dataset.hasData = "1";
      }

      const globalContext = $("globalContext");
      if (globalContext) {
        const health = p.market_health || {};
        const india = health.india || {};
        const global = health.global || {};
      if (PAGE === "all") {
        const gold = data?.GOLD;
        const silver = data?.SILVER;
        const btc = data?.BTC;
        const eth = data?.ETH;
        globalContext.innerHTML = `
          <div class="card">
            <h2>All Markets Snapshot</h2>
            <p><b>India Health:</b> ${esc(fmt(india.status))} (${esc(fmtNum(india.score, 0))}/100)</p>
            <p><b>Global Health:</b> ${esc(fmt(global.status))} (${esc(fmtNum(global.score, 0))}/100)</p>
            <p><b>Gold:</b> ${esc(formatPriceValue(gold?.current_price, "GOLD", gold))} (${esc(fmt(gold?.trend))})</p>
            <p><b>Silver:</b> ${esc(formatPriceValue(silver?.current_price, "SILVER", silver))} (${esc(fmt(silver?.trend))})</p>
            <p><b>BTC:</b> ${esc(formatPriceValue(btc?.current_price, "BTC", btc))} (${esc(fmt(btc?.trend))})</p>
            <p><b>ETH:</b> ${esc(formatPriceValue(eth?.current_price, "ETH", eth))} (${esc(fmt(eth?.trend))})</p>
          </div>
        `;
      } else if (PAGE === "global") {
        globalContext.innerHTML = `
          <div class="card">
            <h2>Global Market Health</h2>
              <p><b>Status:</b> ${esc(fmt(global.status))}</p>
              <p><b>Score:</b> ${esc(fmtNum(global.score, 0))} / 100</p>
              <p>${esc((global.notes || [])[0] || "")}</p>
              <p>${esc((global.notes || [])[1] || "")}</p>
              <p class="muted">Global indices tracked: SP500, NASDAQ, DAX, NIKKEI, HANGSENG.</p>
            </div>
          `;
      } else if (PAGE === "commodities") {
        const gold = data?.GOLD;
        const silver = data?.SILVER;
        globalContext.innerHTML = `
          <div class="card">
            <h2>Commodities Snapshot</h2>
            <p><b>Gold:</b> ${esc(formatPriceValue(gold?.current_price, "GOLD", gold))} (${esc(fmt(gold?.trend))})</p>
            <p><b>Silver:</b> ${esc(formatPriceValue(silver?.current_price, "SILVER", silver))} (${esc(fmt(silver?.trend))})</p>
            <p class="muted">Silver shown in INR/kg (spot). Prices are live when available, otherwise last close.</p>
          </div>
        `;
      } else if (PAGE === "crypto") {
        const btc = data?.BTC;
        const eth = data?.ETH;
        globalContext.innerHTML = `
          <div class="card">
            <h2>Crypto Snapshot</h2>
            <p><b>BTC:</b> ${esc(formatPriceValue(btc?.current_price, "BTC", btc))} (${esc(fmt(btc?.trend))})</p>
            <p><b>ETH:</b> ${esc(formatPriceValue(eth?.current_price, "ETH", eth))} (${esc(fmt(eth?.trend))})</p>
            <p class="muted">Prices are live when available, otherwise last close.</p>
          </div>
        `;
      } else {
          globalContext.innerHTML = renderIndiaMarketHealthCard(data, p);
        }
      }

      const topTrades = $("topTrades");
      renderIndiaRangePreview(data);
      if (topTrades) {
        const strategies = Array.isArray(p.strategies) && p.strategies.length
          ? p.strategies
          : [p.top_trades].filter(Boolean);
        const pageStrategies = strategies.filter((s) => {
          const market = s.market || "india";
          const sid = String(s?.strategy_id || "");
          // Local/experimental scans are India-only.
          if (sid.startsWith("local_") && PAGE !== "india") {
            return false;
          }
          if (PAGE === "all") {
            return true;
          }
          if (PAGE === "commodities") {
            // Commodities page should show only commodity strategies.
            return market === "commodities";
          }
          return market === "all" || market === PAGE;
        }).map((s) => filterIntradayStrategyForPage(s, data, PAGE));
        const mergedMain = mergeIntradayPair(pageStrategies, {
          onPrefix: "intraday_momentum_on",
          waitPrefix: "intraday_momentum_wait",
          mergedId: "intraday_momentum_combined",
          mergedTitle: "Intraday Momentum (VWAP Confirmation)",
          market: "all",
        });
        let mergedStrategies = mergedMain.rest;
        if (mergedMain.merged) {
          mergedStrategies = [mergedMain.merged, ...mergedStrategies];
        }
        const mergedCommodities = mergeIntradayPair(mergedStrategies, {
          onPrefix: "intraday_momentum_commodities_on",
          waitPrefix: "intraday_momentum_commodities_wait",
          mergedId: "intraday_momentum_commodities_combined",
          mergedTitle: "Commodities Intraday Momentum (VWAP Confirmation)",
          market: "commodities",
        });
        mergedStrategies = mergedCommodities.rest;
        if (mergedCommodities.merged) {
          mergedStrategies = [mergedCommodities.merged, ...mergedStrategies];
        }
        strategyCardCount = mergedStrategies.length;
        strategySignalCount = mergedStrategies.reduce((sum, strategy) => {
          const count = Array.isArray(strategy?.items) ? strategy.items.length : 0;
          return sum + count;
        }, 0);

        const cards = mergedStrategies
          .map((s, strategyIndex) => {
            const rawItems = Array.isArray(s?.items) ? s.items : [];
            const sortedItems = rawItems
              .slice()
              .sort((a, b) => {
                const ta = Date.parse(a?.entry_time || a?.signal_time || a?.time || "");
                const tb = Date.parse(b?.entry_time || b?.signal_time || b?.time || "");
                const va = Number.isFinite(ta);
                const vb = Number.isFinite(tb);
                if (va && vb) return tb - ta; // latest first
                if (va) return -1;
                if (vb) return 1;
                return 0;
              });
            const isCommodityBreakout = PAGE === "commodities" && String(s?.strategy_id || "") === "commodities_breakout_retest_on";
            const groupedCommodityItems = [];
            let items = [];
            if (isCommodityBreakout) {
              const byAsset = new Map();
              const commodityOrder = ["GOLD", "SILVER", "CRUDEOIL", "BRENT", "NATGAS", "COPPER", "PLATINUM"];
              sortedItems.forEach((it) => {
                const key = String(it?.ticker || it?.name || "OTHER");
                const curr = byAsset.get(key) || [];
                if (curr.length < 5) curr.push(it); // latest 5 per commodity
                byAsset.set(key, curr);
              });
              byAsset.forEach((assetItems, key) => groupedCommodityItems.push({ key, items: assetItems }));
              groupedCommodityItems.sort((a, b) => {
                const ai = commodityOrder.indexOf(String(a.key).toUpperCase());
                const bi = commodityOrder.indexOf(String(b.key).toUpperCase());
                const av = ai === -1 ? 999 : ai;
                const bv = bi === -1 ? 999 : bi;
                return av - bv;
              });
            } else {
              items = sortedItems.slice(0, 5); // latest 5 overall
            }
            const owner = s?.owner || "HARSHIT";
            const winRatio = s?.win_ratio_pct;
            const winDays = s?.win_ratio_days;
            const winTrades = s?.win_ratio_trades;
            const winWins = s?.win_ratio_wins;
            const universeLine = buildStrategyUniverseLine(s);
            const notes = s?.notes || [];
            const history = Array.isArray(s?.history) ? s.history : [];
            const renderTradeItem = (t) => {
              const lines = (t.lines || []).map(cleanText);
              const detailLines = lines.slice(0, 2);
              const summary = detailLines.length
                ? detailLines.map((line) => `<div class="muted">${esc(line)}</div>`).join("")
                : `<div class="muted">Summary unavailable.</div>`;
              const assetKey = t.ticker || t.symbol || t.name;
              const title = displayLabel(t.name || t.ticker || "Trade");
              const titleHtml = assetKey
                ? `<button class="asset-link" data-asset="${esc(assetKey)}">${esc(title)}</button>`
                : `<span>${esc(title)}</span>`;
              const badgeLabel = instrumentBadgeLabel(t);
              const badgeHtml = badgeLabel
                ? `<span class="trade-badge ${esc(String(t?.instrument_type || "spot").toLowerCase())}">${esc(badgeLabel)}</span>`
                : "";
              return `
                  <div class="trade-item">
                    <div class="trade-title-row">
                      <div class="trade-title">${titleHtml}</div>
                      ${badgeHtml}
                    </div>
                    ${summary}
                  </div>
                `;
            };
            const winLine = winRatio !== null && winRatio !== undefined
              ? `Win ratio: ${fmtNum(winRatio, 1)}%` +
                `${winDays ? ` | Last ${winDays} days` : ""}` +
                `${winTrades ? ` | Trades: ${winTrades}` : ""}` +
                `${winWins ? ` | Wins: ${winWins}` : ""}`
              : null;
            const itemsHtml = isCommodityBreakout
              ? (
                groupedCommodityItems.length
                  ? (() => {
                    const cardId = `commodity-trades-${strategyIndex}`;
                    const buttons = [
                      `<button class="trade-filter-btn active" data-commodity-filter="ALL">All</button>`,
                      ...groupedCommodityItems.map((g) => (
                        `<button class="trade-filter-btn" data-commodity-filter="${esc(g.key)}">${esc(displayLabel(g.key))} (${g.items.length})</button>`
                      )),
                    ].join("");
                    const groups = groupedCommodityItems
                      .map((g) => `
                        <div class="trade-group" data-commodity-group="${esc(g.key)}">
                          <div class="trade-title">${esc(displayLabel(g.key))} (Latest 5)</div>
                          ${g.items.map(renderTradeItem).join("")}
                        </div>
                      `)
                      .join("");
                    return `
                      <div class="commodity-trade-wrap" data-commodity-card="${cardId}">
                        <div class="trade-filter-row">
                          ${buttons}
                        </div>
                        ${groups}
                      </div>
                    `;
                  })()
                  : `<p class="muted">No trades available.</p>`
              )
              : (
                items.length
                  ? items.map(renderTradeItem).join("")
                  : `<p class="muted">No trades available.</p>`
              );

            return `
              <div class="card">
                <h3>${esc(s?.title || "Strategy")}</h3>
                ${s?.trade_type ? `<p class="muted">Type: ${esc(s.trade_type)}</p>` : ""}
                ${owner ? `<p class="muted">By ${esc(owner)}</p>` : ""}
                ${universeLine ? `<p class="strategy-universe-line">${esc(universeLine)}</p>` : ""}
                ${winLine ? `<p class="muted">${esc(winLine)}</p>` : ""}
                ${notes.length ? renderList(notes.map(cleanText), "No notes", 2) : ""}
                ${history.length ? renderList(
                  history.map((h) => {
                    if (typeof h === "string") return h;
                    const tickers = (h.tickers || []).join(", ");
                    const tickersText = tickers ? ` | ${tickers}` : "";
                    const detail = h.detail || `${h.count} items`;
                    return `History ${h.date}: ${detail}${tickersText}`;
                  }),
                  "No history yet",
                  7
                ) : ""}
                ${itemsHtml}
              </div>
            `;
          })
          .join("");

        const telegramJoinUrl = String(window.MP360_SITE_CONFIG?.telegramJoinUrl || "").trim();
        const looksLikeTelegram =
          telegramJoinUrl.toLowerCase().startsWith("https://t.me/") ||
          telegramJoinUrl.toLowerCase().startsWith("http://t.me/");
        const telegramCta = looksLikeTelegram
          ? `<a class="telegram-cta" href="${esc(telegramJoinUrl)}" target="_blank" rel="noopener">Join Telegram Alerts</a>`
          : "";

        topTrades.innerHTML = `
          <div class="strategy-desk-head">
            <div>
              <h2>Strategy Desk</h2>
              <p class="muted">${PAGE === "india" ? "F&O stocks and index companions, in one section." : "Multiple strategies, side by side."}</p>
            </div>
            ${telegramCta}
          </div>
          <div class="strategy-grid">
            ${cards || "<p class=\"muted\">No strategies for this market yet.</p>"}
          </div>
        `;

        if (!topTrades.dataset.bound) {
          topTrades.dataset.bound = "true";
          topTrades.addEventListener("click", (event) => {
            const filterBtn = event.target.closest("[data-commodity-filter]");
            if (filterBtn) {
              const card = filterBtn.closest("[data-commodity-card]");
              if (!card) return;
              const target = String(filterBtn.getAttribute("data-commodity-filter") || "ALL");
              card.querySelectorAll(".trade-filter-btn").forEach((btn) => {
                btn.classList.toggle("active", btn === filterBtn);
              });
              card.querySelectorAll("[data-commodity-group]").forEach((group) => {
                const groupKey = String(group.getAttribute("data-commodity-group") || "");
                const show = target === "ALL" || groupKey === target;
                group.style.display = show ? "" : "none";
              });
              return;
            }
            const el = event.target.closest("[data-asset]");
            if (!el) return;
            const raw = el.getAttribute("data-asset");
            const key = resolveAssetKey(data, raw);
            if (!key) return;
            const item = data[key];
            if (item) showAssetDetails(key, item);
          });
        }
      }

      const indiaIndexEntries = INDIA_INDICES.filter((name) => data[name]).map((name) => [name, data[name]]);
      const globalIndexEntries = GLOBAL_INDICES.filter((name) => data[name]).map((name) => [name, data[name]]);
      const commodityEntries = COMMODITIES.filter((name) => data[name]).map((name) => [name, data[name]]);
      const cryptoSymbolEntries = CRYPTO.filter((name) => data[name]).map((name) => [name, data[name]]);

      const moverEntries = PAGE === "global"
        ? [...globalIndexEntries, ...globalStockEntries]
        : PAGE === "all"
          ? [...indiaIndexEntries, ...indiaStockEntries, ...globalIndexEntries, ...globalStockEntries, ...commodityEntries, ...cryptoSymbolEntries]
        : PAGE === "commodities"
          ? [...commodityEntries]
          : PAGE === "crypto"
            ? [...cryptoSymbolEntries]
            : [...indiaIndexEntries, ...indiaStockEntries];

      const movers = moverEntries
        .filter(([, v]) => Array.isArray(v?.history) && v.history.length >= 6)
        .map(([name, v]) => ({
          name,
          one: pctChange(v.history, 1),
          five: pctChange(v.history, 5),
          twentyOne: pctChange(v.history, 21)
        }))
        .filter((x) => x.one !== null);

      const topGainers = $("topGainers");
      if (topGainers) {
        const gainers = movers
          .slice()
          .sort((a, b) => b.one - a.one)
          .slice(0, 5);
        const rows = gainers.map((g) => [
          `<button class="asset-link" data-asset="${esc(g.name)}">${esc(displayLabel(g.name))}</button>`,
          formatChange(g.one),
          formatChange(g.five),
          formatChange(g.twentyOne)
        ]);
        topGainers.innerHTML = `
          <h2>Top Gainers</h2>
          ${renderTable(["Asset", "1D", "1W", "1M"], rows)}
          <small class="muted">Top 5 by 1-day move.</small>
        `;
        topGainers.addEventListener("click", (event) => {
          const el = event.target.closest("[data-asset]");
          if (!el) return;
          const raw = el.getAttribute("data-asset");
          const key = resolveAssetKey(data, raw);
          if (!key) return;
          const item = data[key];
          if (item) showAssetDetails(key, item);
        });
      }

      const topLosers = $("topLosers");
      if (topLosers) {
        const losers = movers
          .slice()
          .sort((a, b) => a.one - b.one)
          .slice(0, 5);
        const rows = losers.map((l) => [
          `<button class="asset-link" data-asset="${esc(l.name)}">${esc(displayLabel(l.name))}</button>`,
          formatChange(l.one),
          formatChange(l.five),
          formatChange(l.twentyOne)
        ]);
        topLosers.innerHTML = `
          <h2>Top Losers</h2>
          ${renderTable(["Asset", "1D", "1W", "1M"], rows)}
          <small class="muted">Top 5 by 1-day drawdown.</small>
        `;
        topLosers.addEventListener("click", (event) => {
          const el = event.target.closest("[data-asset]");
          if (!el) return;
          const raw = el.getAttribute("data-asset");
          const key = resolveAssetKey(data, raw);
          if (!key) return;
          const item = data[key];
          if (item) showAssetDetails(key, item);
        });
      }

      const eventsSection = $("eventsSection");
      if (eventsSection) {
        const profiles = p.event_context?.profiles || [];
        eventCount = profiles.length;
        const profilesHtml = profiles.length
          ? profiles
              .slice(0, 3)
              .map(
                (e) => `
              <div class="event-item">
                <div class="event-title">${esc(e.name || "Event")}</div>
                <div class="muted">${esc(fmt(e.class))} · ${esc(fmt(e.date))}</div>
                <div class="muted">${esc(e.narrative?.observation || "")}</div>
              </div>
            `
              )
              .join("")
          : `<p class="muted">No active event profiles.</p>`;

        eventsSection.innerHTML = `
          <h2>Events</h2>
          <p><b>Trigger:</b> ${esc(fmt(p.event_context?.trigger))}</p>
          ${profilesHtml}
        `;
      }

      const newsSection = $("newsSection");
      if (newsSection) {
        const news = Array.isArray(p.news) ? p.news : [];
        newsCount = news.length;
        const newsHtml = news.length
          ? news
              .slice(0, 4)
              .map(
                (n) => `
              <div class="news-item">
                <div class="news-title">${esc(n.title || "News")}</div>
                <div class="muted">${esc(n.category || "")}${n.time ? ` · ${esc(n.time)}` : ""}</div>
                ${n.summary ? `<p>${esc(n.summary)}</p>` : ""}
                ${n.url ? `<a href="${esc(n.url)}" target="_blank" rel="noopener">Read more →</a>` : ""}
              </div>
            `
              )
              .join("")
          : `<p class="muted">No news items available.</p>`;

        newsSection.innerHTML = `
          <h2>News</h2>
          ${newsHtml}
        `;
      }

      const globalSearchInput = $("globalSearchInput");
      const globalSearchList = $("globalSearchList");
      const globalSearchOutput = $("globalSearchOutput");
      if (PAGE === "global" && globalSearchInput && globalSearchList && globalSearchOutput) {
        const searchItems = [...globalIndexEntries, ...globalStockEntries].map(([key, value]) => ({
          key,
          label: displayLabel(key),
          value
        }));
        buildSearchList(searchItems, globalSearchList);
        globalSearchInput.oninput = () => {
          const match = matchSearch(searchItems, globalSearchInput.value);
          if (!match) {
            globalSearchOutput.innerHTML = globalSearchInput.value.trim()
              ? `<p class="muted">No matching symbol found.</p>`
              : "";
            return;
          }
          globalSearchOutput.innerHTML = `
            <h3>${esc(displayLabel(match.key))}</h3>
            ${renderAssetDetails(match.key, match.value)}
          `;
        };
      }

      const commoditySearchInput = $("commoditySearchInput");
      const commoditySearchList = $("commoditySearchList");
      const commoditySearchOutput = $("commoditySearchOutput");
      if (PAGE === "commodities" && commoditySearchInput && commoditySearchList && commoditySearchOutput) {
        const searchItems = commodityEntries.map(([key, value]) => ({
          key,
          label: displayLabel(key),
          value
        }));
        buildSearchList(searchItems, commoditySearchList);
        commoditySearchInput.oninput = () => {
          const match = matchSearch(searchItems, commoditySearchInput.value);
          if (!match) {
            commoditySearchOutput.innerHTML = commoditySearchInput.value.trim()
              ? `<p class="muted">No matching commodity found.</p>`
              : "";
            return;
          }
          commoditySearchOutput.innerHTML = `
            <h3>${esc(displayLabel(match.key))}</h3>
            ${renderAssetDetails(match.key, match.value)}
          `;
        };

        const commodityButtons = $("commodityButtons");
        if (commodityButtons) {
          const buttonHtml = COMMODITIES.map((key) => {
            const hasData = !!data[key];
            const label = displayLabel(key);
            return `<button class="chip-button" data-asset="${esc(key)}" ${hasData ? "" : "disabled"}>${esc(label)}</button>`;
          }).join("");
          commodityButtons.innerHTML = buttonHtml || `<p class="muted">No commodities available.</p>`;
          if (!commodityButtons.dataset.bound) {
            commodityButtons.dataset.bound = "true";
            commodityButtons.addEventListener("click", (event) => {
              const el = event.target.closest("[data-asset]");
              if (!el) return;
              const key = resolveAssetKey(data, el.getAttribute("data-asset"));
              if (!key || !data[key]) return;
              showAssetDetails(key, data[key]);
            });
          }
        }
      }

      const cryptoSearchInput = $("cryptoSearchInput");
      const cryptoSearchList = $("cryptoSearchList");
      const cryptoSearchOutput = $("cryptoSearchOutput");
      if (PAGE === "crypto" && cryptoSearchInput && cryptoSearchList && cryptoSearchOutput) {
        const searchItems = cryptoSymbolEntries.map(([key, value]) => ({
          key,
          label: displayLabel(key),
          value
        }));
        buildSearchList(searchItems, cryptoSearchList);
        cryptoSearchInput.oninput = () => {
          const match = matchSearch(searchItems, cryptoSearchInput.value);
          if (!match) {
            cryptoSearchOutput.innerHTML = cryptoSearchInput.value.trim()
              ? `<p class="muted">No matching crypto found.</p>`
              : "";
            return;
          }
          cryptoSearchOutput.innerHTML = `
            <h3>${esc(displayLabel(match.key))}</h3>
            ${renderAssetDetails(match.key, match.value)}
          `;
        };
      }

      const allSearchInput = $("allSearchInput");
      const allSearchList = $("allSearchList");
      const allSearchOutput = $("allSearchOutput");
      if (PAGE === "all" && allSearchInput && allSearchList && allSearchOutput) {
        const searchItems = [
          ...indiaIndexEntries,
          ...globalIndexEntries,
          ...commodityEntries,
          ...cryptoSymbolEntries,
          ...indiaStockEntries,
          ...globalStockEntries,
        ].map(([key, value]) => ({
          key,
          label: displayLabel(key),
          value
        }));
        buildSearchList(searchItems, allSearchList);
        allSearchInput.oninput = () => {
          const match = matchSearch(searchItems, allSearchInput.value);
          if (!match) {
            allSearchOutput.innerHTML = allSearchInput.value.trim()
              ? `<p class="muted">No matching asset found.</p>`
              : "";
            return;
          }
          allSearchOutput.innerHTML = `
            <h3>${esc(displayLabel(match.key))}</h3>
            ${renderAssetDetails(match.key, match.value)}
          `;
        };
      }

      const globalStockSelect = $("globalStockSelect");
      if (globalStockSelect) {
        const prev = globalStockSelect.value;
        globalStockSelect.innerHTML = `<option value="">Select Global Stock</option>`;
        globalStockEntries
          .map(([name]) => name)
          .sort()
          .forEach((name) => {
            const opt = document.createElement("option");
            opt.value = name;
            opt.textContent = displayLabel(name);
            globalStockSelect.appendChild(opt);
          });
        globalStockSelect.onchange = () => {
          const item = data[globalStockSelect.value];
          const out = $("globalStockOutput");
          if (out) out.innerHTML = renderAssetDetails(globalStockSelect.value, item);
        };
        if (prev && data[prev]) {
          globalStockSelect.value = prev;
          globalStockSelect.onchange();
        }
      }

      const indiaSearchInput = $("indiaSearchInput");
      const indiaSearchList = $("indiaSearchList");
      const indiaSearchOutput = $("indiaSearchOutput");
      if (PAGE === "india" && indiaSearchInput && indiaSearchList && indiaSearchOutput) {
        const searchItems = [...indiaIndexEntries, ...indiaStockEntries].map(([key, value]) => ({
          key,
          label: displayLabel(key),
          value
        }));
        buildSearchList(searchItems, indiaSearchList);

        const renderMatch = (match) => {
          if (!match) {
            indiaSearchOutput.innerHTML = `<p class="muted">No matching symbol found.</p>`;
            return;
          }
          indiaSearchOutput.innerHTML = `
            <h3>${esc(displayLabel(match.key))}</h3>
            ${renderAssetDetails(match.key, match.value)}
          `;
        };

        indiaSearchInput.oninput = () => {
          const query = indiaSearchInput.value.trim().toUpperCase();
          if (!query) {
            indiaSearchOutput.innerHTML = "";
            return;
          }
          renderMatch(matchSearch(searchItems, query));
        };
      }

      const marketContext = $("marketContext");
      if (marketContext) {
        marketContext.innerHTML = `
          <h2>Market Context</h2>
          <p><b>Dow Confirmation:</b> ${esc(fmt(p.dow_confirmation))}</p>
          <p><b>Risk Trend:</b> ${esc(fmt(p.risk_trend))}</p>
          ${p.regime ? `<p><b>Regime:</b> ${esc(fmt(p.regime.regime))} (${esc(fmt(p.regime.volatility))})</p>` : ""}
          ${renderList(p.action_guidance, "No action guidance", 2)}
        `;
      }

      const smartMoney = $("smartMoney");
      if (smartMoney) {
        smartMoney.innerHTML = `
          <h2>Smart Money</h2>
          <p><b>State:</b> ${esc(fmt(p.smart_money?.state))}</p>
          <p><b>Score:</b> ${esc(fmt(p.smart_money?.score))} / 100</p>
          <p><b>Confidence:</b> ${esc(fmt(p.smart_money?.confidence))}</p>
          ${p.smart_money?.reason ? `<p class="muted">${esc(p.smart_money.reason)}</p>` : ""}
        `;
      }

      const breadthSection = $("breadthSection");
      if (breadthSection) {
        breadthSection.innerHTML = `
          <h2>Market Breadth</h2>
          <p><b>Overall:</b> ${fmtPct(p.breadth?.up_pct)} Up / ${fmtPct(p.breadth?.down_pct)} Down / ${fmtPct(p.breadth?.sideways_pct)} Sideways</p>
          ${breadthStocks ? `<p><b>NIFTY 50 (Tracked):</b> ${fmtPct(breadthStocks.up_pct)} Up / ${fmtPct(breadthStocks.down_pct)} Down</p>` : ""}
          ${breadthBroader ? `<p><b>Broader (Tracked):</b> ${fmtPct(breadthBroader.up_pct)} Up / ${fmtPct(breadthBroader.down_pct)} Down</p>` : ""}
          <div class="section-divider"></div>
          ${renderList(p.context_change, "No breadth change detected", 2)}
        `;
      }

      const eventContext = $("eventContext");
      if (eventContext) {
        eventContext.innerHTML = `
          <h2>Event Context</h2>
          <p><b>Trigger:</b> ${esc(fmt(p.event_context?.trigger))}</p>
          ${renderList((p.event_context?.profiles || []).map((e) => e.name || "Event"), "No events", 2)}
        `;
      }

      const pageAssetCount = PAGE === "global"
        ? globalIndexEntries.length + globalStockEntries.length
        : PAGE === "all"
          ? indiaIndexEntries.length + indiaStockEntries.length + globalIndexEntries.length + globalStockEntries.length + commodityEntries.length + cryptoSymbolEntries.length
        : PAGE === "commodities"
          ? commodityEntries.length
          : PAGE === "crypto"
            ? cryptoSymbolEntries.length
            : indiaIndexEntries.length + indiaStockEntries.length;
      const hasSummaryContent = executiveSummary.filter(Boolean).length >= 2;
      const hasMoverContent = movers.length >= 3;
      const hasStrategyContent = strategyCardCount > 0 || strategySignalCount > 0;
      const hasEditorialContent = newsCount > 0 || eventCount > 0;
      const contentRich = pageAssetCount >= 3
        && (hasSummaryContent || hasMoverContent || hasStrategyContent || hasEditorialContent);

      if (contentRich) {
        announcePublisherContentReady({
          page: PAGE,
          pageAssetCount,
          movers: movers.length,
          strategyCards: strategyCardCount,
          strategySignals: strategySignalCount,
          news: newsCount,
          events: eventCount
        });
      } else {
        announcePublisherContentEmpty(`insufficient-content:${PAGE}:${pageAssetCount}`);
      }
    };
    if (lastGeneratedAt && p.generated_at === lastGeneratedAt) {
      applyLivePrices(data).then(() => {
        syncRealtimeKeySignals(data);
        syncActiveDetailPanels(data);
        updateLiveIndicator(data);
      });
      return;
    }
    render();
    lastGeneratedAt = p.generated_at;
    applyLivePrices(data).then(() => {
      syncRealtimeKeySignals(data);
      syncActiveDetailPanels(data);
      updateLiveIndicator(data);
    });
  } catch (err) {
    console.error("FETCH ERROR", err);
    const root = $("liveIntelligence");
    if (root && root.dataset.hasData !== "1") {
      announcePublisherContentEmpty(String(err?.message || err || "fetch-error"));
      root.innerHTML = `
        <section class="grid-2">
          <div class="card">
            <h3>Loading Market Data</h3>
            <p class="muted">Data fetch is retrying automatically every few seconds.</p>
            <p class="muted">If this persists, hard refresh once (Cmd+Shift+R).</p>
          </div>
          <div class="card">
            <h3>Status</h3>
            <p class="muted">Reason: ${esc(String(err?.message || err || "unknown"))}</p>
          </div>
        </section>
      `;
    }
  } finally {
    refreshInFlight = false;
  }
}

initTheme();
refreshData();
bindSuggestionForm();
updateClocks();

if (AUTO_REFRESH_MS > 0) {
  setInterval(() => {
    refreshData();
  }, AUTO_REFRESH_MS);
}

setInterval(() => {
  updateMarketStatus();
  updateClocks();
}, 1000);

document.addEventListener("visibilitychange", () => {
  if (!document.hidden) {
    refreshData();
  }
});

window.addEventListener("focus", () => {
  refreshData();
});
