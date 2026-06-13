const $ = (id) => document.getElementById(id);

const esc = (value) => String(value ?? "")
  .replace(/&/g, "&amp;")
  .replace(/</g, "&lt;")
  .replace(/>/g, "&gt;")
  .replace(/\"/g, "&quot;")
  .replace(/'/g, "&#39;");

const fmt = (value, fallback = "—") => {
  if (value === null || value === undefined || value === "") return fallback;
  return value;
};

const fmtNum = (value, digits = 2) => {
  if (typeof value !== "number") return fmt(value);
  return value.toLocaleString(undefined, { maximumFractionDigits: digits });
};

const fmtPct = (value) => {
  if (typeof value !== "number") return fmt(value);
  return `${value.toFixed(1)}%`;
};

const renderList = (items, emptyLabel = "No data", maxItems = null) => {
  if (!Array.isArray(items) || !items.length) {
    return `<p class="muted">${esc(emptyLabel)}</p>`;
  }
  const sliced = maxItems ? items.slice(0, maxItems) : items;
  return `<ul class="list">${sliced.map((item) => `<li>${esc(item)}</li>`).join("")}</ul>`;
};

const renderMetric = (label, value) => `
  <div class="metric">
    <label>${esc(label)}</label>
    <span>${esc(fmt(value))}</span>
  </div>
`;

const renderTable = (headers, rows, emptyLabel = "No data") => {
  if (!rows.length) {
    return `<p class="muted">${esc(emptyLabel)}</p>`;
  }
  return `
    <table>
      <thead>
        <tr>${headers.map((header) => `<th>${esc(header)}</th>`).join("")}</tr>
      </thead>
      <tbody>
        ${rows.map((cells) => `
          <tr>
            ${cells.map((cell) => `<td>${cell}</td>`).join("")}
          </tr>
        `).join("")}
      </tbody>
    </table>
  `;
};

const displayLabel = (value) => {
  if (!value) return "";
  const raw = String(value)
    .replace(/^\^/, "")
    .replace(/\.(NS|BO|HK|DE|T|L|PA|TO|KS|TW|AX|SW|SR|SS|SZ)$/i, "");
  const map = {
    SP500: "S&P 500",
    NASDAQ: "NASDAQ Composite",
    DAX: "DAX 40",
    NIKKEI: "Nikkei 225",
    HANGSENG: "Hang Seng",
    FTSE100: "FTSE 100",
    CAC40: "CAC 40",
    TSXCOMP: "S&P/TSX Composite",
    KOSPI: "KOSPI",
    TAIEX: "TAIEX",
    ASX200: "ASX 200",
    SMI: "SMI",
    TASI: "Tadawul All Share",
    SSECOMP: "SSE Composite",
    SZSECOMP: "SZSE Component",
    INDIA_VIX: "India VIX",
    BANKNIFTY: "NIFTY Bank",
    NIFTY: "NIFTY 50",
    SENSEX: "BSE Sensex",
    BRENT: "Brent Crude",
    NATGAS: "Natural Gas",
  };
  return map[raw] || raw;
};

const assetDisplayName = (key, item = null) => item?.display_name || item?.label || displayLabel(key);

const normalizeTicker = (value) => {
  if (!value) return "";
  return String(value)
    .trim()
    .toUpperCase()
    .replace(/^\^/, "")
    .replace(/\.(NS|BO|HK|DE|T|L|PA|TO|KS|TW|AX|SW|SR|SS|SZ)$/i, "")
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
  const upper = normalizeTicker(rawKey);
  const keys = Object.keys(data);
  for (const key of keys) {
    if (normalizeTicker(key) === upper) return key;
  }
  for (const key of keys) {
    const label = assetDisplayName(key, data[key]).toUpperCase();
    if (label === upper) return key;
  }
  for (const key of keys) {
    const label = assetDisplayName(key, data[key]).toUpperCase();
    if (label.includes(upper)) return key;
  }
  return null;
};

const pctChange = (input, days) => {
  const item = Array.isArray(input) ? { history: input } : (input || {});
  const history = Array.isArray(item.history) ? item.history : [];
  const structuredRange = item.day_range || item.session_range || item.ohlc || item.intraday || item.price_action || null;

  if (days === 1) {
    const current = getFirstFiniteNumber(item, ["current_price", "current", "close", "ltp", "price"])
      ?? getFirstFiniteNumber(structuredRange, ["current", "last", "ltp", "close", "price"]);
    const previous = getFirstFiniteNumber(structuredRange, ["previous_close", "prev_close", "previous"])
      ?? (history.length >= 2 ? history[history.length - 2]?.close : null);
    if (typeof current === "number" && typeof previous === "number" && previous !== 0) {
      return ((current / previous) - 1) * 100;
    }
  }

  if (!Array.isArray(history) || history.length <= days) return null;
  const latest = history[history.length - 1]?.close;
  const previous = history[history.length - 1 - days]?.close;
  if (typeof latest !== "number" || typeof previous !== "number" || previous === 0) return null;
  return ((latest / previous) - 1) * 100;
};

const liveSessionTrend = (item) => {
  const current = getFirstFiniteNumber(item, ["current_price", "current", "close", "ltp", "price"]);
  const dayRange = item?.day_range || item?.session_range || item?.ohlc || item?.intraday || item?.price_action || null;
  const previous = getFirstFiniteNumber(dayRange, ["previous_close", "prev_close", "previous"])
    ?? getFirstFiniteNumber(item, ["previous_close", "prev_close", "previous"]);
  if (typeof current !== "number" || typeof previous !== "number" || previous === 0) return null;
  if (current > previous) return "PRIMARY_UPTREND";
  if (current < previous) return "PRIMARY_DOWNTREND";
  return "RANGE";
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
  entries.forEach(([, value]) => {
    if (value?.trend === "PRIMARY_UPTREND") up += 1;
    else if (value?.trend === "PRIMARY_DOWNTREND") down += 1;
    else side += 1;
  });
  const total = up + down + side;
  return {
    up_pct: total ? (up / total) * 100 : 0,
    down_pct: total ? (down / total) * 100 : 0,
    sideways_pct: total ? (side / total) * 100 : 0,
    up,
    down,
    side,
    total,
  };
};

const summarizeTrends = (entries) => {
  const up = [];
  const down = [];
  const side = [];
  entries.forEach(([key, value]) => {
    if (value?.trend === "PRIMARY_UPTREND") up.push(assetDisplayName(key, value));
    else if (value?.trend === "PRIMARY_DOWNTREND") down.push(assetDisplayName(key, value));
    else side.push(assetDisplayName(key, value));
  });
  return { up, down, side, total: entries.length };
};

const currencyLabelFor = (key, item) => {
  if (item?.unit) return item.unit;
  if (item?.currency) return item.currency;
  if (key === "SILVER") return "INR/kg";
  if (key === "GOLD") return "INR/10g";
  const type = item?.type;
  if (type === "CRYPTO") return "USD";
  if (type === "GLOBAL_STOCK") return "USD";
  if (type === "INDIA_STOCK") return "INR";
  if (type === "COMMODITY") return "USD";
  if (type === "INDEX") {
    if (["NIFTY", "BANKNIFTY", "SENSEX", "INDIA_VIX"].includes(key)) return "INR";
    return "USD";
  }
  return "";
};

const formatPriceValue = (value, key, item, digits = 2) => {
  if (typeof value !== "number") return fmt(value);
  const label = currencyLabelFor(key, item);
  const num = fmtNum(value, digits);
  return label ? `${num} ${label}` : num;
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

const renderExecutiveSignalRow = (label, text) => {
  if (!text) return "";
  return `
    <div class="exec-signal-row">
      <strong>${esc(label)}</strong>
      <p>${esc(text)}</p>
    </div>
  `;
};

const renderExecutiveWatchPill = (label, value) => `
  <div class="exec-watch-pill">
    <label>${esc(label)}</label>
    <strong>${esc(fmt(value))}</strong>
  </div>
`;

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
  signalRowsHtml = "",
  watchHtml = "",
  signalTitle = "What Matters Now",
  watchTitle = "Radar",
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

const buildExecutiveAssetCard = (label, key, item, options = {}) => {
  const oneDay = pctChange(item, 1);
  const oneWeek = pctChange(item, 5);
  const tone = options.tone || executiveToneFromBalance(
    typeof oneDay === "number" && oneDay > 0 ? 1 : 0,
    typeof oneDay === "number" && oneDay < 0 ? 1 : 0,
    item?.trend === "PRIMARY_UPTREND" ? "bull" : item?.trend === "PRIMARY_DOWNTREND" ? "bear" : "neutral"
  );
  return {
    label,
    tone,
    badge: options.badge || executiveTrendLabel(item?.trend),
    primary: formatPriceValue(item?.current_price, key, item, options.digits ?? 2),
    secondary: options.secondary || `${item?.price_source || "EOD"}${item?.price_timestamp ? ` · ${item.price_timestamp}` : ""}`,
    metrics: [
      { label: "1D", value: formatChange(oneDay), html: true },
      { label: "1W", value: formatChange(oneWeek), html: true },
    ],
    note: options.note || `${label} is the current anchor symbol for this exchange page.`,
  };
};

const renderAssetDetails = (key, item) => {
  if (!item) return `<p class="muted">Select an asset to view details.</p>`;

  const priceMeta = item.price_timestamp
    ? `<p class="muted">Price time: ${esc(item.price_timestamp)} (${esc(item.price_source || "EOD")})</p>`
    : `<p class="muted">Price source: ${esc(item.price_source || "EOD")}</p>`;

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

  const ranges = item.ranges
    ? renderTable(
        ["Horizon", "Low", "Median", "High", "Samples"],
        Object.entries(item.ranges).map(([horizon, range]) => [
          esc(horizon),
          esc(fmtPct(range.low_pct)),
          esc(fmtPct(range.median_pct)),
          esc(fmtPct(range.high_pct)),
          esc(fmtNum(range.samples, 0)),
        ])
      )
    : `<p class="muted">Ranges not available.</p>`;

  const rr = item.risk_reward
    ? renderTable(
        ["Horizon", "Upside", "Downside", "RR", "Samples"],
        Object.entries(item.risk_reward).map(([horizon, range]) => [
          esc(horizon),
          esc(fmtPct(range.upside_pct)),
          esc(fmtPct(range.downside_pct)),
          esc(fmtNum(range.rr_ratio, 2)),
          esc(fmtNum(range.samples, 0)),
        ])
      )
    : `<p class="muted">Risk–reward not available.</p>`;

  const historyRows = Array.isArray(item.history)
    ? item.history.slice(-6).map((row) => [
        esc(row.date),
        esc(formatPriceValue(row.close, key, item)),
      ])
    : [];

  const sr = item.support_resistance;
  const srBlock = sr
    ? `
      <h4>Support / Resistance</h4>
      <div class="metric-row">
        ${renderMetric("Support (Near)", formatPriceValue(sr.support_near, key, item))}
        ${renderMetric("Resistance (Near)", formatPriceValue(sr.resistance_near, key, item))}
        ${renderMetric("Support (Major)", formatPriceValue(sr.support_major, key, item))}
        ${renderMetric("Resistance (Major)", formatPriceValue(sr.resistance_major, key, item))}
      </div>
      <p class="muted">Lookback ${esc(fmt(sr.lookback_days))} sessions, window ${esc(fmt(sr.pivot_window))}.</p>
    `
    : "";

  return `
    <div class="metric-row">
      ${renderMetric("Trend", item.trend)}
      ${renderMetric("Price", formatPriceValue(item.current_price, key, item))}
      ${renderMetric("Freshness", item.freshness || "—")}
    </div>
    ${priceMeta}
    ${emaBlock}
    ${srBlock}
    <h4>Risk–Reward</h4>
    ${rr}
    <h4>Historical Ranges</h4>
    ${ranges}
    <h4>Recent Closes</h4>
    ${historyRows.length ? renderTable(["Date", "Close"], historyRows) : `<p class="muted">History not available.</p>`}
  `;
};

const THEMES = ["royal", "dark-royal", "sage", "beige", "sunrise", "slate", "silver"];
const DEFAULT_THEME = "royal";
const IST_TIMEZONE = "Asia/Kolkata";
const ET_TIMEZONE = "America/New_York";
const PAGE = document.body?.dataset?.page || "world";
const EXCHANGE_ID = document.body?.dataset?.exchange || "";
const FALLBACK_LIVE_API_URL = typeof window !== "undefined" && window.location
  ? `${window.location.origin}/live`
  : null;
const LIVE_API_URL = window?.LIVE_API_URL || "http://localhost:8765/live";
const FALLBACK_UNIVERSE_API_URL = typeof window !== "undefined" && window.location
  ? `${window.location.origin}/universe`
  : null;
const UNIVERSE_API_URL = window?.UNIVERSE_API_URL || "http://localhost:8765/universe";
const AUTO_REFRESH_MS = typeof window !== "undefined" && window.AUTO_REFRESH_MS !== undefined
  ? Number(window.AUTO_REFRESH_MS)
  : 10000;
const CONTENT_READY_EVENT = "mp360:content-ready";
const CONTENT_EMPTY_EVENT = "mp360:content-empty";
const MONTHS = {
  Jan: 0, Feb: 1, Mar: 2, Apr: 3, May: 4, Jun: 5,
  Jul: 6, Aug: 7, Sep: 8, Oct: 9, Nov: 10, Dec: 11,
};

const applyTheme = (theme) => {
  const body = document.body;
  const root = document.documentElement;
  if (!body || !root) return;
  THEMES.forEach((name) => {
    body.classList.remove(`theme-${name}`);
    root.classList.remove(`theme-${name}`);
  });
  body.classList.add(`theme-${theme}`);
  root.classList.add(`theme-${theme}`);
  try {
    localStorage.setItem("mc_theme", theme);
  } catch (_) {
    // ignore
  }
};

const initTheme = () => {
  let theme = DEFAULT_THEME;
  try {
    const saved = localStorage.getItem("mc_theme");
    const normalized = saved === "pista" ? "sage" : saved;
    if (normalized && THEMES.includes(normalized)) theme = normalized;
  } catch (_) {
    // ignore
  }
  applyTheme(theme);
  const select = $("themeSelect");
  if (select) {
    select.value = theme;
    select.addEventListener("change", () => {
      if (THEMES.includes(select.value)) applyTheme(select.value);
    });
  }
};

const announcePublisherContentReady = (detail = {}) => {
  document.dispatchEvent(new CustomEvent(CONTENT_READY_EVENT, {
    detail: { contentRich: true, ...detail },
  }));
};

const announcePublisherContentEmpty = (reason = "no-content") => {
  document.dispatchEvent(new CustomEvent(CONTENT_EMPTY_EVENT, {
    detail: { contentRich: false, reason: String(reason || "no-content") },
  }));
};

const parseGeneratedAt = (value) => {
  if (!value) return null;
  const iso = Date.parse(value);
  if (!Number.isNaN(iso)) return iso;
  const match = String(value).match(/^(\d{2})\s+([A-Za-z]{3})\s+(\d{4}),\s+(\d{1,2}):(\d{2})(?::(\d{2}))?\s+(AM|PM)\s+IST$/);
  if (!match) return null;
  const day = Number(match[1]);
  const month = MONTHS[match[2]];
  const year = Number(match[3]);
  let hour = Number(match[4]);
  const minute = Number(match[5]);
  const second = Number(match[6] || 0);
  const ampm = match[7];
  if (ampm === "PM" && hour < 12) hour += 12;
  if (ampm === "AM" && hour === 12) hour = 0;
  if (month === undefined) return null;
  return Date.UTC(year, month, day, hour - 5, minute - 30, second);
};

const formatDateTimeInZone = (ms, timeZone, label) => {
  if (!ms) return "";
  const formatter = new Intl.DateTimeFormat("en-GB", {
    timeZone,
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: true,
  });
  const parts = formatter.formatToParts(new Date(ms));
  const map = {};
  parts.forEach((part) => {
    if (part.type !== "literal") map[part.type] = part.value;
  });
  return `${map.day || ""} ${map.month || ""} ${map.year || ""}, ${map.hour || ""}:${map.minute || ""}:${map.second || ""} ${(map.dayPeriod || "").toUpperCase()}${label ? ` ${label}` : ""}`.trim();
};

const getZonedNowUtc = (timeZone) => {
  const now = new Date();
  const formatter = new Intl.DateTimeFormat("en-GB", {
    timeZone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
  const parts = formatter.formatToParts(now);
  const map = {};
  parts.forEach((part) => {
    if (part.type !== "literal") map[part.type] = part.value;
  });
  return new Date(Date.UTC(
    Number(map.year),
    Number(map.month) - 1,
    Number(map.day),
    Number(map.hour),
    Number(map.minute),
    Number(map.second)
  ));
};

const formatCountdown = (ms) => {
  if (!Number.isFinite(ms) || ms <= 0) return "00:00:00";
  const total = Math.floor(ms / 1000);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const pad = (value) => String(value).padStart(2, "0");
  return `${pad(h)}:${pad(m)}:${pad(s)}`;
};

const computeMarketSession = (cfg) => {
  if (!cfg || cfg.alwaysLive) {
    return { status: "LIVE", label: "Always on", target: null, detail: cfg?.detail || "Always on" };
  }
  const now = getZonedNowUtc(cfg.timeZone);
  const day = now.getUTCDay();
  const openTime = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate(), cfg.openHour, cfg.openMinute, 0));
  const closeTime = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate(), cfg.closeHour, cfg.closeMinute, 0));
  const weekend = day === 0 || day === 6;

  if (weekend) {
    const nextDay = day === 6 ? 2 : 1;
    const nextOpen = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate() + nextDay, cfg.openHour, cfg.openMinute, 0));
    return { status: "CLOSED", label: "Opens in", target: nextOpen, detail: `${cfg.shortLabel} closed for weekend` };
  }
  if (now < openTime) {
    return { status: "CLOSED", label: "Opens in", target: openTime, detail: `${cfg.shortLabel} opens at ${cfg.openText}` };
  }
  if (now >= openTime && now <= closeTime) {
    return { status: "LIVE", label: "Closes in", target: closeTime, detail: `${cfg.shortLabel} live (${cfg.openText}-${cfg.closeText})` };
  }
  const nextDay = day === 5 ? 3 : 1;
  const nextOpen = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate() + nextDay, cfg.openHour, cfg.openMinute, 0));
  return { status: "CLOSED", label: "Opens in", target: nextOpen, detail: `${cfg.shortLabel} closed` };
};

const formatTimeZone = (timeZone) => new Intl.DateTimeFormat("en-GB", {
  timeZone,
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false,
}).format(new Date());

const setConnectionState = (ok, labelText = "Data Ready") => {
  const liveDot = $("liveStatusDot");
  const liveLabel = $("liveStatusLabel");
  if (!liveDot || !liveLabel) return;
  liveDot.classList.toggle("live", ok);
  liveDot.classList.toggle("offline", !ok);
  liveLabel.textContent = ok ? labelText : "Offline";
  liveLabel.classList.toggle("chip-live", ok);
  liveLabel.classList.toggle("chip-offline", !ok);
};

const updateLiveIndicator = (data) => {
  const liveDot = $("liveStatusDot");
  const liveLabel = $("liveStatusLabel");
  if (!liveDot || !liveLabel) return;
  const isLive = Boolean(data?.__live_status?.ok);
  const isStale = Boolean(data?.__live_status?.stale);
  const showLive = isLive;
  liveDot.classList.toggle("live", showLive);
  liveDot.classList.toggle("offline", !showLive);
  liveLabel.classList.toggle("chip-live", showLive);
  liveLabel.classList.toggle("chip-offline", !showLive);
  if (showLive) {
    liveLabel.textContent = isStale ? "Live (stale)" : "Live";
  } else {
    liveLabel.textContent = "Offline";
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
let exchangeUniverseManifestPromise = null;

const fetchExchangeUniverseManifest = async () => {
  if (exchangeUniverseManifestPromise) return exchangeUniverseManifestPromise;
  const urls = [UNIVERSE_API_URL];
  if (FALLBACK_UNIVERSE_API_URL && FALLBACK_UNIVERSE_API_URL !== UNIVERSE_API_URL) {
    urls.push(FALLBACK_UNIVERSE_API_URL);
  }
  exchangeUniverseManifestPromise = (async () => {
    let lastError = null;
    for (const base of urls) {
      try {
        const res = await fetchWithTimeout(`${base}?ts=${Date.now()}`, { cache: "no-store" }, 4000);
        if (!res.ok) {
          lastError = new Error(`status_${res.status}`);
          continue;
        }
        return await res.json();
      } catch (err) {
        lastError = err;
      }
    }
    throw lastError || new Error("universe_fetch_failed");
  })();
  return exchangeUniverseManifestPromise;
};

const buildLiveUniverseEntry = (asset, assetType = "GLOBAL_STOCK") => ({
  type: asset?.type || assetType,
  trend: null,
  current_price: null,
  ranges: null,
  risk_reward: null,
  support_resistance: null,
  ema9: null,
  last_updated: null,
  price_source: "LIVE_UNIVERSE",
  price_timestamp: null,
  day_range: null,
  history: [],
  freshness: "Live",
  exchange_id: asset?.exchange_id || "",
  symbol: asset?.symbol || asset?.key || "",
  display_name: asset?.label || asset?.display_name || "",
  currency: asset?.currency || "",
  featured: Boolean(asset?.featured),
  live_only: true,
});

const mergeUniverseAsset = (data, asset, assetType = "GLOBAL_STOCK") => {
  if (!data || !asset) return;
  const key = asset.key || asset.symbol;
  if (!key) return;
  const base = buildLiveUniverseEntry(asset, assetType);
  data[key] = data[key]
    ? {
      ...base,
      ...data[key],
      exchange_id: data[key].exchange_id || base.exchange_id,
      symbol: data[key].symbol || base.symbol,
      display_name: data[key].display_name || base.display_name,
      currency: data[key].currency || base.currency,
      featured: Boolean(data[key].featured || base.featured),
      live_only: data[key].live_only ?? base.live_only,
    }
    : base;
};

const mergeExchangeUniverseData = (data, cfg, manifest) => {
  if (!data || !cfg || !manifest?.exchanges?.[cfg.id] || !isExchangeLaunchReady(cfg)) return data;
  const exchange = manifest.exchanges[cfg.id] || {};
  const benchmark = exchange.benchmark;
  if (benchmark?.key) {
    mergeUniverseAsset(data, {
      key: benchmark.key,
      symbol: benchmark.symbol,
      label: benchmark.label,
      exchange_id: cfg.id,
      currency: benchmark.currency,
      type: "INDEX",
    }, "INDEX");
  }
  (exchange.stocks || []).forEach((asset) => {
    mergeUniverseAsset(data, asset, asset?.type || "GLOBAL_STOCK");
  });
  return data;
};

const mergeAllExchangeUniverseData = (data, manifest) => {
  if (!data || !manifest?.exchanges) return data;
  EXCHANGE_ORDER.forEach((id) => {
    const cfg = EXCHANGES[id];
    if (isExchangeLaunchReady(cfg)) {
      mergeExchangeUniverseData(data, cfg, manifest);
    }
  });
  return data;
};

const fetchAndMergeLiveKeys = async (data, keys = []) => {
  if (!data || !LIVE_API_URL) return false;
  const unique = uniqueKeys(keys);
  if (!unique.length) return false;
  const urls = [LIVE_API_URL];
  if (FALLBACK_LIVE_API_URL && FALLBACK_LIVE_API_URL !== LIVE_API_URL) {
    urls.push(FALLBACK_LIVE_API_URL);
  }
  let lastError = null;
  for (const base of urls) {
    try {
      const url = `${base}?symbols=${encodeURIComponent(unique.join(","))}`;
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
      if (payload?.summary) {
        data.__live_summary = payload.summary;
        if (payload.summary.breadth) {
          data.breadth = payload.summary.breadth;
          if (window.__exchangePayload) window.__exchangePayload.breadth = payload.summary.breadth;
        }
        if (payload.summary.market_health) {
          data.market_health = payload.summary.market_health;
          if (window.__exchangePayload) window.__exchangePayload.market_health = payload.summary.market_health;
        }
        if (payload.summary.executive_summary) {
          data.executive_summary = payload.summary.executive_summary;
          if (window.__exchangePayload) window.__exchangePayload.executive_summary = payload.summary.executive_summary;
        }
      }
      const prices = payload?.prices || {};
      Object.entries(prices).forEach(([key, value]) => {
        if (!data[key] || value?.price === null || value?.price === undefined) return;
        data[key] = {
          ...data[key],
          current_price: value.price,
          price_source: "LIVE",
          price_timestamp: value.timestamp || payload?.timestamp,
          day_range: value?.day_range || data[key]?.day_range,
          freshness: "Live",
        };
        const liveTrend = liveSessionTrend(data[key]);
        if (liveTrend) data[key].trend = liveTrend;
      });
      return true;
    } catch (err) {
      lastError = err;
    }
  }
  const graceMs = 120000;
  if (lastLiveOkAt && Date.now() - lastLiveOkAt <= graceMs) {
    data.__live_status = { ok: true, checked_at: lastLiveOkTs, stale: true };
  } else {
    data.__live_status = { ok: false, error: lastError ? String(lastError) : "unknown" };
  }
  return false;
};

const collectWorldLiveKeys = (data) => uniqueKeys(
  EXCHANGE_ORDER
    .filter((id) => isExchangeLaunchReady(EXCHANGES[id]))
    .map((id) => EXCHANGES[id]?.benchmarkKey)
    .filter((key) => key && data?.[key])
);

const collectExchangeLiveKeys = (data, cfg, entries = []) => {
  if (PAGE === "world") {
    return collectWorldLiveKeys(data);
  }
  if (!isExchangeLaunchReady(cfg)) {
    return [];
  }
  const keys = uniqueKeys([cfg?.benchmarkKey, cfg?.secondaryKey]);
  const activeQuery = $("exchangeSearchInput")?.value?.trim();
  if (activeQuery) {
    const activeKey = resolveAssetKey(data, activeQuery);
    if (activeKey) keys.push(activeKey);
  }
  return uniqueKeys(keys);
};

const applyExchangeLivePrices = async (data, cfg, entries = []) => {
  if (!data || !LIVE_API_URL) return;
  const liveKeys = collectExchangeLiveKeys(data, cfg, entries);
  if (!liveKeys.length) return;
  await fetchAndMergeLiveKeys(data, liveKeys);
};

const INDIA_PROXY_SYMBOLS = [
  "RELIANCE", "TCS", "HDFCBANK", "ICICIBANK", "INFY", "HINDUNILVR", "ITC", "LT", "SBIN",
  "KOTAKBANK", "AXISBANK", "BAJFINANCE", "TITAN", "ASIANPAINT", "MARUTI", "SUNPHARMA",
  "ULTRACEMCO", "NTPC", "POWERGRID", "M&M", "BHARTIARTL",
];

const GLOBAL_STOCK_EXCHANGE_MAP = {
  AAPL: "nasdaq",
  MSFT: "nasdaq",
  NVDA: "nasdaq",
  AMZN: "nasdaq",
  GOOGL: "nasdaq",
  GOOG: "nasdaq",
  META: "nasdaq",
  TSLA: "nasdaq",
  AVGO: "nasdaq",
  COST: "nasdaq",
  NFLX: "nasdaq",
  AMD: "nasdaq",
  INTC: "nasdaq",
  CSCO: "nasdaq",
  QCOM: "nasdaq",
  ADBE: "nasdaq",
  TXN: "nasdaq",
  ORCL: "nyse",
  PEP: "nasdaq",
  "BRK-B": "nyse",
  LLY: "nyse",
  JPM: "nyse",
  V: "nyse",
  MA: "nyse",
  UNH: "nyse",
  XOM: "nyse",
  HD: "nyse",
  PG: "nyse",
  JNJ: "nyse",
  MRK: "nyse",
  ABBV: "nyse",
  CRM: "nyse",
  BAC: "nyse",
  WMT: "nyse",
  MCD: "nyse",
  NKE: "nyse",
  DIS: "nyse",
  BMY: "nyse",
  CAT: "nyse",
  GE: "nyse",
  IBM: "nyse",
  HON: "nyse",
  UNP: "nyse",
  UPS: "nyse",
  PM: "nyse",
  TMO: "nyse",
  RTX: "nyse",
  LIN: "nyse",
  LOW: "nyse",
  KO: "nyse",
};

const getIndiaStockKeys = (data) => Object.entries(data || {})
  .filter(([, value]) => value?.type === "INDIA_STOCK")
  .map(([key]) => key);

const getExchangeKeysById = (data, exchangeId) => Object.entries(data || {})
  .filter(([key, value]) => {
    if (!value) return false;
    if (value?.exchange_id) return value.exchange_id === exchangeId;
    return value?.type === "GLOBAL_STOCK" && GLOBAL_STOCK_EXCHANGE_MAP[key] === exchangeId;
  })
  .map(([key]) => key);

const getGlobalKeysByExchange = (data, exchangeId) => getExchangeKeysById(data, exchangeId);

const uniqueKeys = (keys) => Array.from(new Set((keys || []).filter(Boolean)));
const pickEntries = (data, keys) => uniqueKeys(keys).filter((key) => data[key]).map((key) => [key, data[key]]);

const EXCHANGE_STATUS_META = {
  live: { label: "Live coverage", chipClass: "chip-live" },
  proxy: { label: "Proxy coverage", chipClass: "chip-neutral" },
  index: { label: "Index-first", chipClass: "chip-neutral" },
  planned: { label: "Onboarding", chipClass: "chip-offline" },
  upcoming: { label: "Upcoming", chipClass: "chip-offline" },
};

const VALIDATED_LIVE_EXCHANGES = new Set([
  "nse",
]);

const getEffectiveExchangeStatus = (cfg) => {
  if (!cfg) return "upcoming";
  if (VALIDATED_LIVE_EXCHANGES.has(cfg.id)) {
    return cfg.status || "live";
  }
  return "upcoming";
};

const isExchangeLaunchReady = (cfg) => getEffectiveExchangeStatus(cfg) === "live";

const EXCHANGES = {
  nse: {
    id: "nse",
    shortLabel: "NSE",
    name: "National Stock Exchange of India",
    region: "India",
    timeZone: IST_TIMEZONE,
    timeZoneLabel: "IST",
    openHour: 9,
    openMinute: 15,
    closeHour: 15,
    closeMinute: 30,
    openText: "09:15 IST",
    closeText: "15:30 IST",
    status: "live",
    market: "india",
    benchmarkKey: "NIFTY",
    secondaryKey: "BANKNIFTY",
    searchPlaceholder: "Type NSE symbol or index (e.g., RELIANCE, NIFTY)",
    description: "Primary India trading page anchored to NIFTY, BANKNIFTY, India VIX and the current India stock universe.",
    notes: [
      "Live large-cap India coverage with strategy support.",
      "Best page for India radar, alerts and NIFTY-led context.",
    ],
    collectKeys: (data) => uniqueKeys(["NIFTY", "BANKNIFTY", "INDIA_VIX", ...getIndiaStockKeys(data)]),
  },
  bse: {
    id: "bse",
    shortLabel: "BSE",
    name: "Bombay Stock Exchange",
    region: "India",
    timeZone: IST_TIMEZONE,
    timeZoneLabel: "IST",
    openHour: 9,
    openMinute: 15,
    closeHour: 15,
    closeMinute: 30,
    openText: "09:15 IST",
    closeText: "15:30 IST",
    status: "live",
    market: "india",
    benchmarkKey: "SENSEX",
    secondaryKey: "NIFTY",
    searchPlaceholder: "Type BSE ticker or index (e.g., RELIANCE.BO, TCS.BO, SENSEX)",
    description: "BSE page anchored to Sensex with direct BSE large-cap symbols wired for live price action.",
    notes: [
      "Sensex benchmark and tracked BSE large caps are now live on this page.",
      "Search any listed BSE symbol from the tracked universe to load live details.",
    ],
    collectKeys: (data) => uniqueKeys(["SENSEX", "NIFTY", ...getExchangeKeysById(data, "bse")]),
  },
  nyse: {
    id: "nyse",
    shortLabel: "NYSE",
    name: "New York Stock Exchange",
    region: "United States",
    timeZone: ET_TIMEZONE,
    timeZoneLabel: "ET",
    openHour: 9,
    openMinute: 30,
    closeHour: 16,
    closeMinute: 0,
    openText: "09:30 ET",
    closeText: "16:00 ET",
    status: "live",
    market: "global",
    benchmarkKey: "SP500",
    secondaryKey: null,
    searchPlaceholder: "Type NYSE ticker (e.g., JPM, BRK-B, XOM)",
    description: "NYSE page for the mapped US large-cap universe with a stable live blue-chip anchor.",
    notes: [
      "A stable live blue-chip anchor is used for the page headline.",
      "Good base for US quality, financials, industrials and defensive leaders.",
    ],
    collectKeys: (data) => uniqueKeys(["SP500", ...getGlobalKeysByExchange(data, "nyse")]),
  },
  nasdaq: {
    id: "nasdaq",
    shortLabel: "Nasdaq",
    name: "Nasdaq Stock Market",
    region: "United States",
    timeZone: ET_TIMEZONE,
    timeZoneLabel: "ET",
    openHour: 9,
    openMinute: 30,
    closeHour: 16,
    closeMinute: 0,
    openText: "09:30 ET",
    closeText: "16:00 ET",
    status: "live",
    market: "global",
    benchmarkKey: "NASDAQ",
    secondaryKey: null,
    searchPlaceholder: "Type Nasdaq ticker (e.g., AAPL, NVDA, MSFT)",
    description: "Nasdaq page focused on the mapped US growth and technology universe with a stable live tech anchor.",
    notes: [
      "A stable live tech anchor is used for the page headline.",
      "Strategy cards are filtered down to mapped Nasdaq names only.",
    ],
    collectKeys: (data) => uniqueKeys(["NASDAQ", ...getGlobalKeysByExchange(data, "nasdaq")]),
  },
  lse: {
    id: "lse",
    shortLabel: "LSE",
    name: "London Stock Exchange",
    region: "United Kingdom",
    timeZone: "Europe/London",
    timeZoneLabel: "UK",
    openHour: 8,
    openMinute: 0,
    closeHour: 16,
    closeMinute: 30,
    openText: "08:00 UK",
    closeText: "16:30 UK",
    status: "live",
    market: null,
    benchmarkKey: "FTSE100",
    searchPlaceholder: "Type LSE ticker or company (e.g., AZN, SHEL, HSBC)",
    description: "Dedicated UK exchange page with FTSE context and a live tracked LSE leaders universe.",
    notes: [
      "FTSE benchmark is wired into the live exchange layer.",
      "Tracked UK leaders can be searched for live open, low, high and current price.",
    ],
    collectKeys: (data) => uniqueKeys(["FTSE100", ...getExchangeKeysById(data, "lse")]),
  },
  hkex: {
    id: "hkex",
    shortLabel: "HKEX",
    name: "Hong Kong Stock Exchange",
    region: "Hong Kong",
    timeZone: "Asia/Hong_Kong",
    timeZoneLabel: "HKT",
    openHour: 9,
    openMinute: 30,
    closeHour: 16,
    closeMinute: 0,
    openText: "09:30 HKT",
    closeText: "16:00 HKT",
    status: "live",
    market: null,
    benchmarkKey: "HANGSENG",
    searchPlaceholder: "Type HKEX ticker or company (e.g., 0005, 0700, 9988)",
    description: "Hang Seng-led HKEX page with tracked Hong Kong constituents available for live search.",
    notes: [
      "Hang Seng benchmark is live now through the exchange live layer.",
      "Tracked HKEX constituents can now be opened on demand with live session details.",
    ],
    collectKeys: (data) => uniqueKeys(["HANGSENG", ...getExchangeKeysById(data, "hkex")]),
  },
  tse: {
    id: "tse",
    shortLabel: "TSE",
    name: "Tokyo Stock Exchange",
    region: "Japan",
    timeZone: "Asia/Tokyo",
    timeZoneLabel: "JST",
    openHour: 9,
    openMinute: 0,
    closeHour: 15,
    closeMinute: 0,
    openText: "09:00 JST",
    closeText: "15:00 JST",
    status: "live",
    market: null,
    benchmarkKey: "NIKKEI",
    searchPlaceholder: "Type TSE ticker (e.g., 1332, 7203, 9983)",
    description: "Nikkei-led Tokyo page with tracked Japan constituents ready for live lookup.",
    notes: [
      "Nikkei benchmark stays live on page refresh.",
      "Tracked Tokyo constituents are available through on-demand live detail fetch.",
    ],
    collectKeys: (data) => uniqueKeys(["NIKKEI", ...getExchangeKeysById(data, "tse")]),
  },
  sse: {
    id: "sse",
    shortLabel: "SSE",
    name: "Shanghai Stock Exchange",
    region: "China",
    timeZone: "Asia/Shanghai",
    timeZoneLabel: "CST",
    openHour: 9,
    openMinute: 30,
    closeHour: 15,
    closeMinute: 0,
    openText: "09:30 CST",
    closeText: "15:00 CST",
    status: "live",
    market: null,
    benchmarkKey: "SSECOMP",
    searchPlaceholder: "Type SSE ticker or company (e.g., 600519, 601318)",
    description: "Shanghai exchange page with a live China A-shares proxy anchor and a tracked Shanghai leaders universe.",
    notes: [
      "A live China A-shares proxy is used as the benchmark anchor for this page.",
      "Tracked Shanghai leaders can be searched for live session detail now.",
    ],
    collectKeys: (data) => uniqueKeys(["SSECOMP", ...getExchangeKeysById(data, "sse")]),
  },
  szse: {
    id: "szse",
    shortLabel: "SZSE",
    name: "Shenzhen Stock Exchange",
    region: "China",
    timeZone: "Asia/Shanghai",
    timeZoneLabel: "CST",
    openHour: 9,
    openMinute: 30,
    closeHour: 15,
    closeMinute: 0,
    openText: "09:30 CST",
    closeText: "15:00 CST",
    status: "live",
    market: null,
    benchmarkKey: "SZSECOMP",
    searchPlaceholder: "Type SZSE ticker or company (e.g., 002594, 300750)",
    description: "Shenzhen exchange page with a live growth anchor and tracked growth leaders.",
    notes: [
      "A live Shenzhen growth anchor is used for the page headline so the view stays active.",
      "Tracked Shenzhen names can be searched for live details now.",
    ],
    collectKeys: (data) => uniqueKeys(["SZSECOMP", ...getExchangeKeysById(data, "szse")]),
  },
  xetra: {
    id: "xetra",
    shortLabel: "Xetra",
    name: "Deutsche Borse Xetra",
    region: "Germany",
    timeZone: "Europe/Berlin",
    timeZoneLabel: "CET",
    openHour: 9,
    openMinute: 0,
    closeHour: 17,
    closeMinute: 30,
    openText: "09:00 CET",
    closeText: "17:30 CET",
    status: "live",
    market: null,
    benchmarkKey: "DAX",
    searchPlaceholder: "Type Xetra ticker or company (e.g., SAP, ALV, BMW)",
    description: "DAX-led Xetra page with tracked German constituents available for live search.",
    notes: [
      "DAX benchmark stays live through the exchange live layer.",
      "Tracked Xetra constituents are available for on-demand session detail.",
    ],
    collectKeys: (data) => uniqueKeys(["DAX", ...getExchangeKeysById(data, "xetra")]),
  },
  euronext: {
    id: "euronext",
    shortLabel: "Euronext",
    name: "Euronext Paris",
    region: "Europe",
    timeZone: "Europe/Paris",
    timeZoneLabel: "CET",
    openHour: 9,
    openMinute: 0,
    closeHour: 17,
    closeMinute: 30,
    openText: "09:00 CET",
    closeText: "17:30 CET",
    status: "live",
    market: null,
    benchmarkKey: "CAC40",
    searchPlaceholder: "Type Euronext ticker or company (e.g., MC, OR, AIR)",
    description: "Euronext Paris page with CAC 40 context and a live tracked leaders universe.",
    notes: [
      "CAC 40 context is wired into the live universe layer.",
      "Tracked Paris leaders can now be searched for live session details.",
    ],
    collectKeys: (data) => uniqueKeys(["CAC40", ...getExchangeKeysById(data, "euronext")]),
  },
  tsx: {
    id: "tsx",
    shortLabel: "TSX",
    name: "Toronto Stock Exchange",
    region: "Canada",
    timeZone: "America/Toronto",
    timeZoneLabel: "ET",
    openHour: 9,
    openMinute: 30,
    closeHour: 16,
    closeMinute: 0,
    openText: "09:30 ET",
    closeText: "16:00 ET",
    status: "live",
    market: null,
    benchmarkKey: "TSXCOMP",
    searchPlaceholder: "Type TSX ticker or company (e.g., RY, TD, SHOP)",
    description: "Toronto exchange page with a live Canada market anchor and a tracked leaders universe.",
    notes: [
      "A live Canada market anchor is used for the page headline so the view stays active.",
      "Tracked Canadian leaders can be searched for live session details.",
    ],
    collectKeys: (data) => uniqueKeys(["TSXCOMP", ...getExchangeKeysById(data, "tsx")]),
  },
  krx: {
    id: "krx",
    shortLabel: "KRX",
    name: "Korea Exchange",
    region: "South Korea",
    timeZone: "Asia/Seoul",
    timeZoneLabel: "KST",
    openHour: 9,
    openMinute: 0,
    closeHour: 15,
    closeMinute: 30,
    openText: "09:00 KST",
    closeText: "15:30 KST",
    status: "live",
    market: null,
    benchmarkKey: "KOSPI",
    searchPlaceholder: "Type KRX ticker or company (e.g., 005930, 000660)",
    description: "Korea Exchange page with a live Korea equity proxy anchor and tracked South Korea leaders.",
    notes: [
      "A live Korea equity proxy is used for the page headline so the view stays active.",
      "Tracked Korea leaders can now be searched for live price-action detail.",
    ],
    collectKeys: (data) => uniqueKeys(["KOSPI", ...getExchangeKeysById(data, "krx")]),
  },
  twse: {
    id: "twse",
    shortLabel: "TWSE",
    name: "Taiwan Stock Exchange",
    region: "Taiwan",
    timeZone: "Asia/Taipei",
    timeZoneLabel: "CST",
    openHour: 9,
    openMinute: 0,
    closeHour: 13,
    closeMinute: 30,
    openText: "09:00 CST",
    closeText: "13:30 CST",
    status: "live",
    market: null,
    benchmarkKey: "TAIEX",
    searchPlaceholder: "Type TWSE ticker or company (e.g., 2330, 2317)",
    description: "Taiwan exchange page with a live Taiwan equity proxy anchor and tracked Taiwan leaders.",
    notes: [
      "A live Taiwan equity proxy is used for the page headline so the view stays active.",
      "Tracked Taiwan leaders can now be searched for live session details.",
    ],
    collectKeys: (data) => uniqueKeys(["TAIEX", ...getExchangeKeysById(data, "twse")]),
  },
  asx: {
    id: "asx",
    shortLabel: "ASX",
    name: "Australian Securities Exchange",
    region: "Australia",
    timeZone: "Australia/Sydney",
    timeZoneLabel: "AEST",
    openHour: 10,
    openMinute: 0,
    closeHour: 16,
    closeMinute: 0,
    openText: "10:00 AEST",
    closeText: "16:00 AEST",
    status: "live",
    market: null,
    benchmarkKey: "ASX200",
    searchPlaceholder: "Type ASX ticker or company (e.g., BHP, CBA, CSL)",
    description: "ASX page with benchmark context and live tracked Australia leaders.",
    notes: [
      "ASX benchmark context is wired into the live exchange layer.",
      "Tracked Australia leaders can now be searched for live session details.",
    ],
    collectKeys: (data) => uniqueKeys(["ASX200", ...getExchangeKeysById(data, "asx")]),
  },
  six: {
    id: "six",
    shortLabel: "SIX",
    name: "SIX Swiss Exchange",
    region: "Switzerland",
    timeZone: "Europe/Zurich",
    timeZoneLabel: "CET",
    openHour: 9,
    openMinute: 0,
    closeHour: 17,
    closeMinute: 30,
    openText: "09:00 CET",
    closeText: "17:30 CET",
    status: "live",
    market: null,
    benchmarkKey: "SMI",
    searchPlaceholder: "Type SIX ticker or company (e.g., NESN, ROG, NOVN)",
    description: "Swiss exchange page with SMI context and live tracked Switzerland leaders.",
    notes: [
      "SMI context is wired into the exchange live layer.",
      "Tracked Swiss leaders can now be searched for live session details.",
    ],
    collectKeys: (data) => uniqueKeys(["SMI", ...getExchangeKeysById(data, "six")]),
  },
  tadawul: {
    id: "tadawul",
    shortLabel: "Tadawul",
    name: "Saudi Exchange Tadawul",
    region: "Saudi Arabia",
    timeZone: "Asia/Riyadh",
    timeZoneLabel: "AST",
    openHour: 10,
    openMinute: 0,
    closeHour: 15,
    closeMinute: 0,
    openText: "10:00 AST",
    closeText: "15:00 AST",
    status: "live",
    market: null,
    benchmarkKey: "TASI",
    searchPlaceholder: "Type Tadawul ticker or company (e.g., 2222, 1120, 7010)",
    description: "Saudi exchange page with a live Saudi market anchor and tracked leaders.",
    notes: [
      "A live Saudi market anchor is used for the page headline so the view stays active.",
      "Tracked Saudi leaders can now be searched for live session details.",
    ],
    collectKeys: (data) => uniqueKeys(["TASI", ...getExchangeKeysById(data, "tadawul")]),
  },
};

const EXCHANGE_ORDER = [
  "nse", "bse", "nyse", "nasdaq", "lse", "hkex", "tse", "sse", "szse",
  "xetra", "euronext", "tsx", "krx", "twse", "asx", "six", "tadawul",
];

const getExchangeConfig = () => EXCHANGES[EXCHANGE_ID] || null;
const getExchangeEntries = (data, cfg) => {
  if (!isExchangeLaunchReady(cfg)) return [];
  return pickEntries(data, cfg?.collectKeys ? cfg.collectKeys(data) : []);
};

const buildUniverseSet = (entries) => {
  const set = new Set();
  entries.forEach(([key]) => {
    set.add(String(key).toUpperCase());
    set.add(normalizeTicker(key));
  });
  return set;
};

const isItemInUniverse = (item, universe) => {
  if (!item || !universe.size) return false;
  const candidates = [item.ticker, item.symbol, item.name].filter(Boolean).map((candidate) => String(candidate).trim());
  return candidates.some((candidate) => universe.has(candidate.toUpperCase()) || universe.has(normalizeTicker(candidate)));
};

const filterStrategyForUniverse = (strategy, universe) => {
  if (!strategy) return strategy;
  const items = Array.isArray(strategy.items) ? strategy.items.filter((item) => isItemInUniverse(item, universe)) : [];
  const history = Array.isArray(strategy.history)
    ? strategy.history.map((row) => {
        if (!row || typeof row === "string") return row;
        const tickers = Array.isArray(row.tickers)
          ? row.tickers.filter((ticker) => universe.has(String(ticker).toUpperCase()) || universe.has(normalizeTicker(ticker)))
          : [];
        return { ...row, tickers, count: tickers.length || row.count || 0 };
      }).filter((row) => {
        if (typeof row === "string") return true;
        if (!row) return false;
        const hasTickers = Array.isArray(row.tickers) && row.tickers.length > 0;
        return hasTickers || Number(row.count || 0) > 0;
      })
    : [];
  return { ...strategy, items, history };
};

const renderStrategyCards = (payload, cfg, universe) => {
  const root = $("strategyDesk");
  if (!root) return { cards: 0, signals: 0 };
  const status = getEffectiveExchangeStatus(cfg);
  const allStrategies = Array.isArray(payload?.strategies) ? payload.strategies : [];
  if (!cfg?.market || status === "upcoming") {
    root.innerHTML = `
      <h2>Strategy Desk</h2>
      <p class="muted">${status === "upcoming" ? "Strategy automation is paused because this exchange is marked Upcoming." : "Strategy automation will switch on once this exchange has stock-level universe support."}</p>
      <div class="card-inline-note">Current page status: ${esc(EXCHANGE_STATUS_META[status].label)}.</div>
    `;
    return { cards: 0, signals: 0 };
  }

  const strategies = allStrategies
    .filter((strategy) => (strategy.market || "india") === cfg.market)
    .map((strategy) => filterStrategyForUniverse(strategy, universe))
    .filter((strategy) => {
      if (!Array.isArray(strategy.items) || !strategy.items.length) {
        return cfg.market === "india" || cfg.market === "global" ? true : false;
      }
      return true;
    });

  const cards = strategies.map((strategy) => {
    const items = Array.isArray(strategy.items) ? strategy.items.slice(0, 5) : [];
    const notes = Array.isArray(strategy.notes) ? strategy.notes.slice(0, 2).map(cleanText) : [];
    const history = Array.isArray(strategy.history) ? strategy.history.slice(-5) : [];
    const winLine = strategy.win_ratio_pct !== null && strategy.win_ratio_pct !== undefined
      ? `Win ratio ${fmtNum(strategy.win_ratio_pct, 1)}%${strategy.win_ratio_days ? ` | Last ${strategy.win_ratio_days} days` : ""}`
      : null;
    const itemsHtml = items.length
      ? items.map((item) => {
          const assetKey = item.ticker || item.symbol || item.name;
          const title = assetDisplayName(item.name || item.ticker || "Trade", item);
          const lines = Array.isArray(item.lines) ? item.lines.slice(0, 2).map(cleanText) : [];
          return `
            <div class="trade-item">
              <div class="trade-title">${assetKey ? `<button class="asset-link" data-asset="${esc(assetKey)}">${esc(title)}</button>` : esc(title)}</div>
              ${lines.length ? lines.map((line) => `<div class="muted">${esc(line)}</div>`).join("") : `<div class="muted">Signal available.</div>`}
            </div>
          `;
        }).join("")
      : `<p class="muted">No current symbols inside this exchange universe.</p>`;
    return `
      <div class="card">
        <h3>${esc(strategy.title || "Strategy")}</h3>
        ${strategy.trade_type ? `<p class="muted">Type: ${esc(strategy.trade_type)}</p>` : ""}
        ${winLine ? `<p class="muted">${esc(winLine)}</p>` : ""}
        ${notes.length ? renderList(notes, "No notes", 2) : ""}
        ${history.length ? renderList(history.map((row) => {
          if (typeof row === "string") return row;
          const tickers = Array.isArray(row.tickers) && row.tickers.length ? ` | ${row.tickers.join(", ")}` : "";
          return `History ${row.date}: ${row.detail || `${row.count} items`}${tickers}`;
        }), "No history", 5) : ""}
        ${itemsHtml}
      </div>
    `;
  }).join("");

  root.innerHTML = `
    <h2>Strategy Desk</h2>
    <p class="muted">Strategies are filtered to the ${esc(cfg.shortLabel)} universe where coverage exists.</p>
    <div class="strategy-grid">
      ${cards || `<p class="muted">No strategies available for this exchange yet.</p>`}
    </div>
  `;

  if (!root.dataset.bound) {
    root.dataset.bound = "true";
    root.addEventListener("click", (event) => {
      const element = event.target.closest("[data-asset]");
      if (!element) return;
      const key = resolveAssetKey(window.__exchangeData || {}, element.getAttribute("data-asset"));
      if (!key || !window.__exchangeData?.[key]) return;
      showSearchResult(key, window.__exchangeData[key]);
    });
  }

  return {
    cards: strategies.length,
    signals: strategies.reduce((sum, strategy) => sum + (Array.isArray(strategy.items) ? strategy.items.length : 0), 0),
  };
};

const renderMovers = (elementId, title, movers, emptyLabel) => {
  const el = $(elementId);
  if (!el) return;
  const rows = movers.map((item) => [
    `<button class="asset-link" data-asset="${esc(item.name)}">${esc(assetDisplayName(item.name, window.__exchangeData?.[item.name]))}</button>`,
    formatChange(item.one),
    formatChange(item.five),
    formatChange(item.twentyOne),
  ]);
  el.innerHTML = `
    <h2>${esc(title)}</h2>
    ${renderTable(["Asset", "1D", "1W", "1M"], rows, emptyLabel)}
    <small class="muted">Top 5 by 1-day move.</small>
  `;
  if (!el.dataset.bound) {
    el.dataset.bound = "true";
    el.addEventListener("click", (event) => {
      const asset = event.target.closest("[data-asset]");
      if (!asset) return;
      const key = resolveAssetKey(window.__exchangeData || {}, asset.getAttribute("data-asset"));
      if (!key || !window.__exchangeData?.[key]) return;
      void showSearchResult(key, window.__exchangeData[key]);
    });
  }
};

const showSearchResult = async (key, item) => {
  const input = $("exchangeSearchInput");
  const output = $("exchangeSearchOutput");
  if (!window.__exchangeData?.[key] && item) {
    window.__exchangeData[key] = item;
  }
  if (window.__exchangeData) {
    await fetchAndMergeLiveKeys(window.__exchangeData, [key]);
    item = window.__exchangeData[key] || item;
  }
  if (input) input.value = assetDisplayName(key, item);
  if (output) {
    output.innerHTML = `
      <h3>${esc(assetDisplayName(key, item))}</h3>
      ${renderAssetDetails(key, item)}
    `;
    output.scrollIntoView({ behavior: "smooth", block: "start" });
  }
};

const bindExchangeSearch = (entries, cfg) => {
  const input = $("exchangeSearchInput");
  const list = $("exchangeSearchList");
  const output = $("exchangeSearchOutput");
  if (!input || !list || !output) return;
  input.placeholder = cfg?.searchPlaceholder || input.placeholder;
  const searchItems = entries.map(([key, value]) => ({ key, label: assetDisplayName(key, value), value }));
  const renderMatch = async (query) => {
    const normalized = String(query || "").trim().toUpperCase();
    if (!normalized) {
      output.innerHTML = "";
      return;
    }
    const exact = searchItems.find((item) => item.label.toUpperCase() === normalized || normalizeTicker(item.key) === normalizeTicker(normalized));
    const matches = searchItems.filter((item) => {
      const label = item.label.toUpperCase();
      const ticker = normalizeTicker(item.key);
      return label.includes(normalized) || ticker.includes(normalized);
    });
    const match = exact || (matches.length === 1 ? matches[0] : null);
    if (!match && !matches.length) {
      output.innerHTML = `<p class="muted">No matching asset found for ${esc(cfg.shortLabel)}.</p>`;
      return;
    }
    if (!match && matches.length > 1) {
      output.innerHTML = `<p class="muted">${matches.length} matches found for ${esc(cfg.shortLabel)}. Select a tighter ticker or choose from suggestions.</p>`;
      return;
    }
    await showSearchResult(match.key, match.value);
  };
  list.innerHTML = searchItems.map((item) => `<option value="${esc(item.label)}"></option>`).join("");
  input.oninput = () => {
    void renderMatch(input.value);
  };
  input.onchange = () => {
    void renderMatch(input.value);
  };
  if (input.value.trim()) {
    void renderMatch(input.value);
  }
};

const renderEvents = (payload) => {
  const el = $("eventsSection");
  if (!el) return 0;
  const profiles = Array.isArray(payload?.event_context?.profiles) ? payload.event_context.profiles.slice(0, 4) : [];
  el.innerHTML = `
    <h2>Events</h2>
    <p><b>Trigger:</b> ${esc(fmt(payload?.event_context?.trigger))}</p>
    ${profiles.length ? profiles.map((profile) => `
      <div class="event-item">
        <div class="event-title">${esc(profile.name || "Event")}</div>
        <div class="muted">${esc(fmt(profile.class))} · ${esc(fmt(profile.date))}</div>
        <div class="muted">${esc(profile.narrative?.observation || "")}</div>
      </div>
    `).join("") : `<p class="muted">No active event profiles.</p>`}
  `;
  return profiles.length;
};

const renderNews = (payload) => {
  const el = $("newsSection");
  if (!el) return 0;
  const news = Array.isArray(payload?.news) ? payload.news.slice(0, 4) : [];
  el.innerHTML = `
    <h2>News</h2>
    ${news.length ? news.map((item) => `
      <div class="news-item">
        <div class="news-title">${esc(item.title || "News")}</div>
        <div class="muted">${esc(item.category || "")} ${item.time ? `· ${esc(item.time)}` : ""}</div>
        ${item.summary ? `<p>${esc(item.summary)}</p>` : ""}
        ${item.url ? `<a href="${esc(item.url)}" target="_blank" rel="noopener">Read more →</a>` : ""}
      </div>
    `).join("") : `<p class="muted">No news items available.</p>`}
  `;
  return news.length;
};

const renderWorldLanding = (payload) => {
  const data = payload?.data || {};
  window.__exchangeData = data;
  window.__exchangePayload = payload;
  const directory = $("exchangeDirectory");
  const roadmap = $("exchangeCoverage");
  const lastUpdated = $("lastUpdated");
  const parsed = parseGeneratedAt(payload?.generated_at);
  if (lastUpdated) {
    lastUpdated.textContent = parsed
      ? `Last Updated: ${formatDateTimeInZone(parsed, IST_TIMEZONE, "IST")}`
      : `Last Updated: ${fmt(payload?.generated_at)}`;
  }

  const cards = EXCHANGE_ORDER.map((id) => {
    const cfg = EXCHANGES[id];
    const status = getEffectiveExchangeStatus(cfg);
    const entries = getExchangeEntries(data, cfg);
    const benchmarkKey = cfg.benchmarkKey;
    const benchmark = status === "live" && benchmarkKey ? data[benchmarkKey] : null;
    const statusMeta = EXCHANGE_STATUS_META[status];
    const trackedStocks = entries.filter(([, value]) => value?.type === "INDIA_STOCK" || value?.type === "GLOBAL_STOCK").length;
    const trackedIndices = entries.filter(([, value]) => value?.type === "INDEX").length;
    return `
      <a class="exchange-card" href="${esc(`${cfg.id}.html`)}">
        <div class="exchange-card-head">
          <div>
            <h3>${esc(cfg.shortLabel)}</h3>
            <p class="muted exchange-card-subtitle">${esc(cfg.name)}</p>
          </div>
          <span class="${esc(statusMeta.chipClass)}">${esc(statusMeta.label)}</span>
        </div>
        <p class="exchange-card-body">${esc(status === "upcoming" ? `Upcoming: ${cfg.shortLabel} will go live after we stabilize its data quality.` : cfg.description)}</p>
        <div class="metric-row">
          ${renderMetric("Region", cfg.region)}
          ${renderMetric("Tracked", trackedStocks + trackedIndices)}
          ${renderMetric("Benchmark", benchmark ? assetDisplayName(benchmarkKey, benchmark) : (cfg.benchmarkLabel || assetDisplayName(benchmarkKey)))}
        </div>
        <p class="muted">${benchmark ? `${assetDisplayName(benchmarkKey, benchmark)}: ${formatPriceValue(benchmark.current_price, benchmarkKey, benchmark)} (${fmt(benchmark.trend)})` : status === "upcoming" ? "Upcoming: this exchange is hidden until live data quality is stable." : "Benchmark feed is waiting on a live price response."}</p>
      </a>
    `;
  }).join("");

  if (directory) {
    directory.innerHTML = `
      <div class="card">
        <h2>Exchange Directory</h2>
        <p class="muted">Only validated exchanges are shown as live. Any exchange with unstable data is marked Upcoming until we fix it properly.</p>
        <div class="exchange-grid">
          ${cards}
        </div>
      </div>
    `;
  }

  const counts = EXCHANGE_ORDER.reduce((acc, id) => {
    const status = getEffectiveExchangeStatus(EXCHANGES[id]);
    acc[status] = (acc[status] || 0) + 1;
    return acc;
  }, {});
  const readyNow = counts.live || 0;
  const total = EXCHANGE_ORDER.length || 1;
  const tone = executiveToneFromBalance(readyNow, counts.upcoming || 0, "neutral");
  const root = $("liveIntelligence");
  if (root) {
    root.dataset.hasData = "1";
    root.innerHTML = renderExecutiveSummaryShell({
      title: "Executive Summary",
      intro: "World Exchanges now shows only the exchange pages that have been locally validated for reliable live data.",
      tone,
      chip: "Quality Gate",
      headline: readyNow ? "Validated exchanges stay live, and unstable exchanges are moved to Upcoming." : "Exchange launch is paused until data quality is validated.",
      note: "This keeps the site honest: we would rather show fewer exchanges with correct data than more exchanges with broken live coverage.",
      score: (readyNow / total) * 100,
      cardsHtml: [
        renderExecutiveInfoCard({
          label: "Live Pages",
          badge: "Ready Now",
          tone: "bull",
          primary: counts.live || 0,
          secondary: "Validated live exchanges",
          metrics: [
            { label: "Exchanges", value: counts.live || 0 },
            { label: "Total", value: EXCHANGE_ORDER.length },
          ],
          note: "These are the only exchanges we currently trust enough to expose as live.",
        }),
        renderExecutiveInfoCard({
          label: "Upcoming",
          badge: "Hold Back",
          tone: counts.upcoming ? "neutral" : "bull",
          primary: counts.upcoming || 0,
          secondary: "Awaiting stable live coverage",
          metrics: [
            { label: "Queued", value: counts.upcoming || 0 },
            { label: "Reason", value: "Data quality" },
          ],
          note: "These exchanges stay visible in the directory, but we mark them Upcoming until their feeds are stable.",
        }),
        renderExecutiveInfoCard({
          label: "Policy",
          badge: "Honest UI",
          tone: "neutral",
          primary: "Quality",
          secondary: "No unreliable live launch",
          metrics: [
            { label: "Rule", value: "Validate first" },
            { label: "Fallback", value: "Upcoming" },
          ],
          note: "If an exchange takes too long to fix, it stays Upcoming instead of shipping with weak or broken live data.",
        }),
        renderExecutiveInfoCard({
          label: "Next Step",
          badge: "Backlog",
          tone: "neutral",
          primary: "Per Exchange",
          secondary: "Fix feeds one by one",
          metrics: [
            { label: "Queued", value: counts.upcoming || 0 },
            { label: "Goal", value: "All major exchanges" },
          ],
          note: "We can promote each exchange back to live once its benchmark and tracked symbols pass validation.",
        }),
      ].join(""),
      footerItems: [
        `Pages: ${EXCHANGE_ORDER.length}`,
        `Live: ${counts.live || 0}`,
        `Upcoming: ${counts.upcoming || 0}`,
        "Validated exchanges only",
      ],
      signalRowsHtml: [
        renderExecutiveSignalRow("Coverage", `Live ${counts.live || 0}, Upcoming ${counts.upcoming || 0}.`),
        renderExecutiveSignalRow("Structure", "Only validated exchanges keep live benchmark and search behavior."),
        renderExecutiveSignalRow("Tactical Read", "Use the live exchanges now, and treat the rest as queued launches until the feeds are fixed."),
      ].join(""),
      watchHtml: [
        renderExecutiveWatchPill("Live Pages", counts.live || 0),
        renderExecutiveWatchPill("Upcoming", counts.upcoming || 0),
        renderExecutiveWatchPill("Primary Base", "NSE"),
        renderExecutiveWatchPill("Mode", "Quality First"),
      ].join(""),
    });
  }

  if (roadmap) {
    roadmap.innerHTML = `
      <div class="card">
        <h2>Launch Policy</h2>
        ${renderList([
          "Only exchanges with stable live coverage stay marked live.",
          "Any exchange with weak or inconsistent feed coverage is marked Upcoming.",
          "We promote exchanges back to live only after benchmark and tracked symbols pass validation.",
          "This keeps the data quality clean for the exchanges that are already working."
        ])}
      </div>
    `;
  }

  const newsCount = renderNews(payload);
  const eventCount = renderEvents(payload);
  updateLiveIndicator(data);
  announcePublisherContentReady({ page: "world", exchanges: EXCHANGE_ORDER.length, news: newsCount, events: eventCount });
};

const renderExchangePage = (payload) => {
  const cfg = getExchangeConfig();
  const root = $("liveIntelligence");
  const hero = $("exchangeHero");
  if (!cfg || !root || !hero) {
    announcePublisherContentEmpty("missing-exchange-config");
    return;
  }

  const data = payload?.data || {};
  window.__exchangeData = data;
  const status = getEffectiveExchangeStatus(cfg);
  const launchReady = isExchangeLaunchReady(cfg);
  const entries = getExchangeEntries(data, cfg);
  const universe = buildUniverseSet(entries);
  const benchmark = launchReady && cfg.benchmarkKey ? data[cfg.benchmarkKey] : null;
  const secondary = launchReady && cfg.secondaryKey ? data[cfg.secondaryKey] : null;
  const stockEntries = entries.filter(([, value]) => value?.type === "INDIA_STOCK" || value?.type === "GLOBAL_STOCK");
  const indexEntries = entries.filter(([, value]) => value?.type === "INDEX");
  const trendSummary = summarizeTrends(entries);
  const breadth = computeBreadth(stockEntries.length ? stockEntries : entries);
  const statusMeta = EXCHANGE_STATUS_META[status];
  const strategyCounts = renderStrategyCards(payload, cfg, universe);
  const parsed = parseGeneratedAt(payload?.generated_at);
  const lastUpdated = $("lastUpdated");
  if (lastUpdated) {
    lastUpdated.textContent = parsed
      ? `Last Updated: ${formatDateTimeInZone(parsed, cfg.timeZone, cfg.timeZoneLabel)}`
      : `Last Updated: ${fmt(payload?.generated_at)}`;
  }

  const benchmarkTone = benchmark?.trend === "PRIMARY_UPTREND"
    ? "bull"
    : benchmark?.trend === "PRIMARY_DOWNTREND"
      ? "bear"
      : executiveToneFromBalance(
        typeof pctChange(benchmark, 1) === "number" && pctChange(benchmark, 1) > 0 ? 1 : 0,
        typeof pctChange(benchmark, 1) === "number" && pctChange(benchmark, 1) < 0 ? 1 : 0,
        status === "upcoming" ? "neutral" : "bull"
      );
  const score = breadth
    ? clampNum(breadth.up_pct, 0, 100, 50)
    : benchmarkTone === "bull"
      ? 68
      : benchmarkTone === "bear"
        ? 32
        : 50;

  root.dataset.hasData = "1";
  root.innerHTML = renderExecutiveSummaryShell({
    title: "Executive Summary",
    intro: launchReady
      ? `${cfg.shortLabel} page blends exchange routing, benchmark context, and filtered strategy coverage where the backend already supports it.`
      : `${cfg.shortLabel} page is visible in the directory, but live launch is paused until data quality is stable.`,
    tone: benchmarkTone,
    chip: statusMeta.label,
    headline: benchmark
      ? `${assetDisplayName(cfg.benchmarkKey, benchmark)} is the lead read for ${cfg.shortLabel} right now.`
      : status === "upcoming"
        ? `${cfg.shortLabel} is marked Upcoming while we stabilize its live data coverage.`
        : `${cfg.shortLabel} is structured and waiting for direct benchmark onboarding.`,
    note: launchReady
      ? firstText(
        cfg.notes?.join(" "),
        cfg.description,
        "Dedicated exchange routing is live locally and will deepen as universes expand."
      )
      : "This exchange stays in Upcoming until benchmark and tracked symbols are stable enough to trust.",
    score,
    cardsHtml: [
      benchmark
        ? renderExecutiveInfoCard(buildExecutiveAssetCard(
          assetDisplayName(cfg.benchmarkKey, benchmark),
          cfg.benchmarkKey,
          benchmark,
          { note: `${assetDisplayName(cfg.benchmarkKey, benchmark)} is the primary benchmark anchor for ${cfg.shortLabel}.` }
        ))
        : renderExecutiveInfoCard({
          label: "Primary Benchmark",
          badge: status === "upcoming" ? "Upcoming" : "Planned",
          tone: "neutral",
          primary: cfg.benchmarkLabel || assetDisplayName(cfg.benchmarkKey) || "Onboarding",
          secondary: status === "upcoming" ? "Live launch paused for data quality" : "Benchmark feed onboarding in progress",
          metrics: [
            { label: "Coverage", value: statusMeta.label },
            { label: "Region", value: cfg.region },
          ],
          note: status === "upcoming"
            ? "We found feed instability here, so the page is held back until the data is reliable."
            : "This exchange page is routed and ready for benchmark activation.",
        }),
      secondary
        ? renderExecutiveInfoCard(buildExecutiveAssetCard(
          assetDisplayName(cfg.secondaryKey, secondary),
          cfg.secondaryKey,
          secondary,
          { note: `${assetDisplayName(cfg.secondaryKey, secondary)} acts as the supporting benchmark for this page.` }
        ))
        : renderExecutiveInfoCard({
          label: "Coverage",
          badge: statusMeta.label,
          tone: status === "upcoming" ? "neutral" : "bull",
          primary: entries.length,
          secondary: `${stockEntries.length} stocks · ${indexEntries.length} indices`,
          metrics: [
            { label: "Stocks", value: stockEntries.length },
            { label: "Indices", value: indexEntries.length },
          ],
          note: launchReady
            ? "Tracked assets will expand automatically as exchange-specific universes are added."
            : "Tracked assets are hidden until the exchange passes our live-data quality gate.",
        }),
      renderExecutiveInfoCard({
        label: "Strategy Desk",
        badge: cfg.market ? humanizeToken(cfg.market) : "Pending",
        tone: !launchReady ? "neutral" : strategyCounts.cards ? "bull" : cfg.market ? "neutral" : "bear",
        primary: strategyCounts.cards || 0,
        secondary: !launchReady
          ? "Will activate after exchange data quality is stable"
          : cfg.market
            ? "Filtered to the mapped exchange universe"
            : "Will activate with stock-level universe support",
        metrics: [
          { label: "Cards", value: strategyCounts.cards || 0 },
          { label: "Signals", value: strategyCounts.signals || 0 },
        ],
        note: !launchReady
          ? "Strategy automation stays off while this exchange is marked Upcoming."
          : cfg.market
          ? "Exchange strategies are filtered down to symbols that belong to this exchange mapping."
          : "Strategy automation will switch on once stock-level coverage is available.",
      }),
    ].join(""),
    footerItems: [
      `Region: ${cfg.region}`,
      `Tracked Assets: ${entries.length}`,
      `Coverage: ${statusMeta.label}`,
      cfg.market ? `Strategy Map: ${cfg.market.toUpperCase()}` : "Strategy Map: Pending",
    ],
    signalRowsHtml: [
      renderExecutiveSignalRow(
        "Market Tone",
        benchmark
          ? `${assetDisplayName(cfg.benchmarkKey, benchmark)} is tagged ${executiveTrendLabel(benchmark.trend).toLowerCase()} with ${formatPriceValue(benchmark.current_price, cfg.benchmarkKey, benchmark)} on the page.`
          : `${cfg.benchmarkLabel || "Benchmark"} is not live yet, so this page is operating as a structured onboarding shell.`
      ),
      renderExecutiveSignalRow(
        "Participation",
        breadth
          ? `${breadth.up} up, ${breadth.down} down, ${breadth.side} sideways inside the currently tracked ${cfg.shortLabel} universe.`
          : status === "upcoming"
            ? `This exchange is currently hidden from live launch while we fix unstable feed coverage.`
            : `Tracking ${entries.length} assets (${stockEntries.length} stocks, ${indexEntries.length} indices) with depth expanding in future backend phases.`
      ),
      renderExecutiveSignalRow(
        "Tactical Read",
        status === "proxy"
          ? "Use this page for Sensex-linked context now, while direct BSE constituent support is still being added."
          : status === "upcoming"
            ? "Treat this as an Upcoming exchange shell for now. We will promote it back to live only after the feed is stable."
            : "Use the benchmark and filtered strategy desk together to focus on the strongest pockets of this exchange."
      ),
    ].join(""),
    watchHtml: [
      renderExecutiveWatchPill("Coverage", statusMeta.label),
      renderExecutiveWatchPill("Region", cfg.region),
      renderExecutiveWatchPill("Tracked", entries.length),
      renderExecutiveWatchPill("Price Time", benchmark?.price_timestamp || payload?.generated_at || "—"),
    ].join(""),
  });

  hero.innerHTML = `
    <div class="card exchange-hero-card">
      <div class="exchange-hero-top">
        <div>
          <h2>${esc(cfg.name)}</h2>
          <p class="muted">${esc(cfg.region)} • ${esc(cfg.openText)} to ${esc(cfg.closeText)}</p>
        </div>
        <span class="${esc(statusMeta.chipClass)}">${esc(statusMeta.label)}</span>
      </div>
      <div class="metric-row">
        ${renderMetric("Primary Benchmark", benchmark ? assetDisplayName(cfg.benchmarkKey, benchmark) : (cfg.benchmarkLabel || assetDisplayName(cfg.benchmarkKey) || "Planned"))}
        ${renderMetric("Price", benchmark ? formatPriceValue(benchmark.current_price, cfg.benchmarkKey, benchmark) : "—")}
        ${renderMetric("Trend", benchmark ? benchmark.trend : "Pending")}
        ${secondary ? renderMetric("Secondary", assetDisplayName(cfg.secondaryKey, secondary)) : ""}
      </div>
      ${secondary ? `<p class="muted">Secondary benchmark: ${esc(assetDisplayName(cfg.secondaryKey, secondary))} at ${esc(formatPriceValue(secondary.current_price, cfg.secondaryKey, secondary))} (${esc(fmt(secondary.trend))}).</p>` : ""}
      ${benchmark?.price_timestamp ? `<p class="muted">Live price time: ${esc(benchmark.price_timestamp)} (${esc(benchmark.price_source || "EOD")})</p>` : status === "upcoming" ? `<p class="muted">Upcoming: live launch is paused until this exchange passes our data-quality checks.</p>` : ""}
      ${launchReady && cfg.notes?.length ? renderList(cfg.notes, "No notes", 3) : status === "upcoming" ? `<p class="muted">Upcoming: we are holding this exchange back until the benchmark and tracked symbols are reliable enough for live launch.</p>` : ""}
    </div>
  `;

  const movers = entries
    .filter(([, value]) => Array.isArray(value?.history) && value.history.length >= 6)
    .map(([name, value]) => ({
      name,
      one: pctChange(value, 1),
      five: pctChange(value, 5),
      twentyOne: pctChange(value, 21),
    }))
    .filter((item) => item.one !== null);

  renderMovers(
    "topGainers",
    "Top Gainers",
    movers.filter((item) => item.one > 0).slice().sort((a, b) => b.one - a.one).slice(0, 5),
    "No gainers available yet."
  );
  renderMovers(
    "topLosers",
    "Top Losers",
    movers.filter((item) => item.one < 0).slice().sort((a, b) => a.one - b.one).slice(0, 5),
    "No losers available yet."
  );
  bindExchangeSearch(entries, cfg);
  const newsCount = renderNews(payload);
  const eventCount = renderEvents(payload);

  const suggestion = $("exchangeFootnote");
  if (suggestion) {
    suggestion.innerHTML = `
      <div class="card">
        <h2>Coverage Notes</h2>
        ${renderList([
          cfg.notes?.[1] || "Dedicated exchange routing is now live locally.",
          status === "proxy"
            ? "This page currently uses proxy constituent coverage until direct exchange feeds are added."
            : status === "upcoming"
              ? "This exchange is marked Upcoming because the live feed needs more work before we trust it."
              : "Search or click a tracked symbol to load live price-action detail from the exchange universe layer.",
          `Current strategy mapping: ${cfg.market ? cfg.market.toUpperCase() : "Not enabled yet"}.`
        ], "No notes")}
      </div>
    `;
  }

  updateLiveIndicator(data);
  if (entries.length || newsCount || eventCount || strategyCounts.cards) {
    announcePublisherContentReady({
      page: cfg.id,
      pageAssetCount: entries.length,
      movers: movers.length,
      strategyCards: strategyCounts.cards,
      strategySignals: strategyCounts.signals,
      news: newsCount,
      events: eventCount,
    });
  } else {
    announcePublisherContentEmpty(`insufficient-content:${cfg.id}`);
  }
};

const updateMarketStatus = () => {
  const label = $("marketStatusLabel");
  const countdown = $("marketCountdown");
  if (!label || !countdown) return;
  if (PAGE === "world") {
    label.textContent = "Mixed Sessions";
    label.classList.add("chip-live");
    label.classList.remove("chip-offline");
    countdown.textContent = "World exchanges view • multiple markets across timezones";
    countdown.classList.add("chip-neutral");
    return;
  }
  const cfg = getExchangeConfig();
  if (!cfg) return;
  const session = computeMarketSession(cfg);
  if (session.status === "LIVE") {
    label.textContent = `Market Open (${cfg.openText}-${cfg.closeText})`;
    label.classList.add("chip-live");
    label.classList.remove("chip-offline");
  } else {
    label.textContent = `Market Closed (${cfg.openText}-${cfg.closeText})`;
    label.classList.add("chip-offline");
    label.classList.remove("chip-live");
  }
  if (session.target) {
    const remaining = session.target.getTime() - getZonedNowUtc(cfg.timeZone).getTime();
    countdown.textContent = `${session.label} ${formatCountdown(remaining)}`;
  } else {
    countdown.textContent = session.detail;
  }
  countdown.classList.add("chip-neutral");
};

const updateClocks = () => {
  const primary = $("clockPrimary");
  const secondary = $("clockSecondary");
  if (!primary || !secondary) return;
  if (PAGE === "world") {
    primary.textContent = `India: ${formatTimeZone(IST_TIMEZONE)} IST`;
    secondary.textContent = `US: ${formatTimeZone(ET_TIMEZONE)} ET`;
    return;
  }
  const cfg = getExchangeConfig();
  if (!cfg) return;
  primary.textContent = `${cfg.shortLabel}: ${formatTimeZone(cfg.timeZone)} ${cfg.timeZoneLabel}`;
  secondary.textContent = cfg.timeZone !== IST_TIMEZONE
    ? `India: ${formatTimeZone(IST_TIMEZONE)} IST`
    : "";
};

const boot = async () => {
  initTheme();
  updateMarketStatus();
  updateClocks();
  try {
    const [response, manifest] = await Promise.all([
      fetch(`data.json?ts=${Date.now()}`, { cache: "no-store" }),
      fetchExchangeUniverseManifest().catch(() => null),
    ]);
    if (!response.ok) {
      throw new Error(`data_fetch_status_${response.status}`);
    }
    const payload = await response.json();
    window.__exchangePayload = payload;
    window.__exchangeData = payload?.data || {};
    window.__exchangeUniverseManifest = manifest;
    if (manifest?.exchanges) {
      if (PAGE === "world") {
        mergeAllExchangeUniverseData(window.__exchangeData, manifest);
      } else {
        mergeExchangeUniverseData(window.__exchangeData, getExchangeConfig(), manifest);
      }
    }
    if (PAGE === "world") {
      await applyExchangeLivePrices(window.__exchangeData, null, []);
      renderWorldLanding(payload);
    } else {
      const cfg = getExchangeConfig();
      const entries = getExchangeEntries(window.__exchangeData, cfg);
      await applyExchangeLivePrices(window.__exchangeData, cfg, entries);
      renderExchangePage(payload);
    }
  } catch (err) {
    console.error("EXCHANGE PAGE ERROR", err);
    setConnectionState(false, "Offline");
    announcePublisherContentEmpty(String(err?.message || err || "fetch-error"));
    const root = $("liveIntelligence");
    if (root) {
      root.innerHTML = `
        <section class="grid-2">
          <div class="card">
            <h3>Loading Exchange Data</h3>
            <p class="muted">The exchange page is retrying automatically.</p>
          </div>
          <div class="card">
            <h3>Status</h3>
            <p class="muted">Reason: ${esc(String(err?.message || err || "unknown"))}</p>
          </div>
        </section>
      `;
    }
  }
};

boot();
setInterval(() => {
  updateMarketStatus();
  updateClocks();
}, 1000);

setInterval(async () => {
  if (!window.__exchangePayload?.data) return;
  try {
    const manifest = window.__exchangeUniverseManifest;
    if (manifest?.exchanges) {
      if (PAGE === "world") {
        mergeAllExchangeUniverseData(window.__exchangePayload.data, manifest);
      } else {
        mergeExchangeUniverseData(window.__exchangePayload.data, getExchangeConfig(), manifest);
      }
    }
    if (PAGE === "world") {
      await applyExchangeLivePrices(window.__exchangePayload.data, null, []);
      renderWorldLanding(window.__exchangePayload);
    } else {
      const cfg = getExchangeConfig();
      const entries = getExchangeEntries(window.__exchangePayload.data, cfg);
      await applyExchangeLivePrices(window.__exchangePayload.data, cfg, entries);
      renderExchangePage(window.__exchangePayload);
    }
  } catch (err) {
    console.error("EXCHANGE LIVE REFRESH ERROR", err);
  }
}, AUTO_REFRESH_MS);
