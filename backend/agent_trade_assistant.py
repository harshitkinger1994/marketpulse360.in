import os
import re
import requests


DEFAULT_OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini"
DEFAULT_GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash").strip() or "gemini-2.0-flash"
LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "").strip().lower()  # "gemini" | "openai"


PROMPT = (
    "CONSOLIDATED SYSTEM PROMPT: Autonomous F&O Execution Parameter Agent\n"
    "You are an expert quantitative trading engine specializing in Indian F&O and Index scaling. "
    "Your task is to autonomously ingest a raw algorithmic strategy log, synthesize the technical parameters, "
    "perform the necessary options and mathematical calculations, and output highly precise execution parameters "
    "optimized for Dhan's Super Order system.\n\n"
    "CRITICAL EXECUTION RULES\n"
    "NO NARRATIVE FLUFF: Output data must start directly with the first ticker line. "
    "Do not include any introductory text, closing remarks, strategy headers, filters, or trade management protocols.\n"
    "STRICT EXCLUSIONS: Completely ignore and omit \"TATASTEEL\" from any analysis or list automatically.\n"
    "INTRADAY LOGIC: Focus purely on execution. Stop-Loss levels must adhere tightly to the structural invalidation boundaries provided in the input log.\n\n"
    "AUTOMATED CALCULATION & INFERENCE ENGINE\n"
    "For each stock in the input log, you must calculate and construct the missing parameters using the following financial logic:\n\n"
    "1. Automated Option Chain & S/R Mapping (Strike Selection)\n"
    "Strike Interval Determination:\n"
    "- Stock Price < 400 -> Round to the nearest interval of 5 or 10\n"
    "- Stock Price 400 to 1000 -> Round to the nearest interval of 10 or 20\n"
    "- Stock Price > 1000 -> Round to the nearest interval of 50 or 100\n"
    "Resistance (R) Strike Calculation: Identify the closest major option strike price above the current close based on the intervals above. This serves as the simulated Max Call OI strike.\n"
    "Support (S) Strike Calculation: Identify the closest major option strike price below the current close based on the intervals above. This serves as the simulated Max Put OI strike.\n\n"
    "2. Autonomous Liquidity Sweep Zone Mapping\n"
    "For SELL Signals: sweep zone = [Support Strike + Interval] - [Support Strike + (Interval * 1.5)].\n"
    "For BUY Signals: sweep zone = [Resistance Strike - (Interval * 1.5)] - [Resistance Strike - Interval].\n\n"
    "3. Dhan Super Order Execution Math\n"
    "Entry / Trigger Price: Must exactly match EntryPx provided in the raw log.\n"
    "Strict Stop-Loss: Set exactly at the Daily EMA9 value provided in the log.\n"
    "Risk% = (abs(EntryPx - EMA9) / EntryPx) * 100.\n"
    "Target 1 (Book 50%): strict 1:1 RR relative to entry and stop-loss.\n"
    "- SELL: Target1 = EntryPx - abs(EntryPx - EMA9)\n"
    "- BUY:  Target1 = EntryPx + abs(EntryPx - EMA9)\n"
    "Target 2: Project to U-Turn Support (shorts) or Resistance (longs). If beyond daily ATR reach, set at 1:2 RR.\n"
    "Potential gains: use the same percentage delta formula.\n\n"
    "MANDATORY OUTPUT LAYOUT (Plaintext)\n"
    "Use short labels only (R/S/Sweep/Entry/SL/T1/T2). Do NOT include strategy/mode/type/filters or any 'Strategy Signal' section.\n"
    "Currency rules: if input Market is INDIA use the rupee symbol (₹). If Market is GLOBAL or CRYPTO use dollar ($). Do not print 'INR'/'USD' words.\n\n"
    "## [STOCK_NAME] | Side: [BUY/SELL]\n"
    "* Sweep: [₹/$][Low] - [₹/$][High] (1 short sentence)\n"
    "* R: [₹/$][Level] -> Why: 1 sentence (simulated Max Call OI / ask wall)\n"
    "* S: [₹/$][Level] -> Why: 1 sentence (simulated Max Put OI / iceberg bids)\n"
    "* Entry: [₹/$][Price]\n"
    "* SL: [₹/$][Price] (Risk: X.XX%)\n"
    "* T1: [₹/$][Price] (Gain: X.XX%)\n"
    "* T2: [₹/$][Price] (Gain: X.XX%)\n"
    "---\n"
)


def _strip_tatasteel(text: str) -> str:
    if not text:
        return ""
    lines = []
    for line in str(text).splitlines():
        if "TATASTEEL" in line.upper():
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def _choose_interval(price: float) -> int:
    """
    Prompt allows multiple common strike steps; pick a deterministic default.
    """
    if price < 400:
        return 10 if price >= 200 else 5
    if price <= 1000:
        return 20 if price >= 700 else 10
    return 100 if price >= 2500 else 50


def _fmt_inr(x: float) -> str:
    return f"{x:.2f}"


def _parse_trade_from_agent_input(input_text: str) -> dict:
    txt = str(input_text or "").strip()
    out = {"ticker": None, "side": None, "entry": None, "ema9": None, "market": None}

    m = re.search(r"^\s*Market:\s*([A-Z_]+)\b", txt, re.M | re.I)
    if m:
        out["market"] = m.group(1).strip().upper()

    # Example: "1) AMBUJACEM | Side: SELL"
    m = re.search(r"^\s*\d+\)\s*([A-Z0-9&_.-]+)\s*\|\s*Side:\s*(BUY|SELL)\b", txt, re.M | re.I)
    if m:
        out["ticker"] = m.group(1).strip().upper()
        out["side"] = m.group(2).strip().upper()

    m = re.search(r"\bEntryPx:\s*([0-9]+(?:\.[0-9]+)?)\b", txt)
    if m:
        try:
            out["entry"] = float(m.group(1))
        except Exception:
            pass

    # Prefer daily EMA9 from "Close ... vs EMA9 X"
    m = re.search(r"\bClose\s*[0-9]+(?:\.[0-9]+)?\s*vs\s*EMA9\s*([0-9]+(?:\.[0-9]+)?)\b", txt, re.I)
    if not m:
        m = re.search(r"\bvs\s*EMA9\s*([0-9]+(?:\.[0-9]+)?)\b", txt, re.I)
    if m:
        try:
            out["ema9"] = float(m.group(1))
        except Exception:
            pass

    return out


def _deterministic_brief(input_text: str) -> str | None:
    """
    Local/offline fallback when LLM is not reachable. Implements the same math the prompt describes.
    """
    t = _parse_trade_from_agent_input(input_text)
    ticker = (t.get("ticker") or "").strip()
    side = (t.get("side") or "").strip().upper()
    entry = t.get("entry")
    ema9 = t.get("ema9")
    if not ticker or side not in {"BUY", "SELL"} or not isinstance(entry, (int, float)) or not isinstance(ema9, (int, float)):
        return None

    interval = _choose_interval(float(entry))
    market = str(t.get("market") or "").strip().upper()
    ccy = "₹" if market == "INDIA" else "$" if market in {"GLOBAL", "CRYPTO"} else ""
    # Use the prompt's "closest strike below/above close" logic.
    import math

    s_strike = math.floor(float(entry) / interval) * interval
    r_strike = math.ceil(float(entry) / interval) * interval
    if s_strike == r_strike:
        r_strike += interval

    # Sweep bands per prompt formulas.
    if side == "SELL":
        sweep_lo = s_strike + interval
        sweep_hi = s_strike + (interval * 1.5)
        sweep_verb = "upside spike"
        sweep_victim = "short"
    else:
        sweep_lo = r_strike - (interval * 1.5)
        sweep_hi = r_strike - interval
        sweep_verb = "downside flush"
        sweep_victim = "long"

    risk = abs(float(entry) - float(ema9))
    risk_pct = (risk / float(entry)) * 100.0 if entry else 0.0

    if side == "SELL":
        t1 = float(entry) - risk
        t2 = float(entry) - (2 * risk)
    else:
        t1 = float(entry) + risk
        t2 = float(entry) + (2 * risk)

    gain1_pct = (abs(t1 - float(entry)) / float(entry)) * 100.0
    gain2_pct = (abs(t2 - float(entry)) / float(entry)) * 100.0

    # Short, consistent "why" lines without extra sections.
    lines = [
        f"## {ticker} | Side: {side}",
        f"* Sweep: {ccy}{_fmt_inr(sweep_lo)} - {ccy}{_fmt_inr(sweep_hi)} (rapid {sweep_verb} hunted retail {sweep_victim} stops; absorption -> snapback)",
        f"* R: {ccy}{_fmt_inr(float(r_strike))} -> Why: simulated Max Call OI / supply wall overhead",
        f"* S: {ccy}{_fmt_inr(float(s_strike))} -> Why: simulated Max Put OI / demand pocket below",
        f"* Entry: {ccy}{_fmt_inr(float(entry))}",
        f"* SL: {ccy}{_fmt_inr(float(ema9))} (Risk: {risk_pct:.2f}%)",
        f"* T1: {ccy}{_fmt_inr(float(t1))} (Gain: {gain1_pct:.2f}%)",
        f"* T2: {ccy}{_fmt_inr(float(t2))} (Gain: {gain2_pct:.2f}%)",
        "---",
    ]
    return "\n".join(lines).strip()


def _openai_brief(input_text: str, *, model: str | None = None, max_output_tokens: int = 450) -> str | None:
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        return None
    chosen_model = (model or DEFAULT_OPENAI_MODEL).strip() or DEFAULT_OPENAI_MODEL
    payload = {
        "model": chosen_model,
        "instructions": PROMPT,
        "input": [{"role": "user", "content": str(input_text or "").strip()}],
        "store": False,
        "max_output_tokens": int(max_output_tokens),
    }
    try:
        resp = requests.post(
            "https://api.openai.com/v1/responses",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=18,
        )
        if resp.status_code != 200:
            return None
        data = resp.json() if resp.content else {}
        text = (data.get("output_text") or "").strip()
        text = _strip_tatasteel(text)
        return re.sub(r"\n{3,}", "\n\n", text).strip() or None
    except Exception:
        return None


def _gemini_brief(input_text: str, *, model: str | None = None, max_output_tokens: int = 450) -> str | None:
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        return None
    chosen_model = (model or DEFAULT_GEMINI_MODEL).strip() or DEFAULT_GEMINI_MODEL
    # Gemini API expects API key via query param (?key=...), not Authorization header.
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{chosen_model}:generateContent?key={api_key}"
    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [{"text": str(input_text or "").strip()}],
            }
        ],
        "systemInstruction": {"parts": [{"text": PROMPT}]},
        "generationConfig": {
            "maxOutputTokens": int(max_output_tokens),
            "temperature": 0.2,
            # Flash models can spend output budget on "thinking" tokens; keep it off for short telegram briefs.
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }
    try:
        resp = requests.post(
            url,
            headers={"Content-Type": "application/json"},
            json=payload,
            timeout=18,
        )
        if resp.status_code != 200:
            return None
        data = resp.json() if resp.content else {}
        # Native REST returns candidates -> content -> parts[{text}]
        candidates = data.get("candidates") or []
        if not candidates:
            return None
        content = (candidates[0] or {}).get("content") or {}
        parts = content.get("parts") or []
        text = "\n".join([p.get("text", "") for p in parts if isinstance(p, dict)]).strip()
        text = _strip_tatasteel(text)
        return re.sub(r"\n{3,}", "\n\n", text).strip() or None
    except Exception:
        return None


def generate_group_brief(input_text: str, *, model: str | None = None, max_output_tokens: int = 450) -> str | None:
    """
    Returns short text for group.
    Provider selection:
      1) LLM_PROVIDER=gemini/openai if set
      2) else prefer GEMINI_API_KEY, then OPENAI_API_KEY
    """
    provider = LLM_PROVIDER
    if provider == "gemini":
        return _gemini_brief(input_text, model=model, max_output_tokens=max_output_tokens) or _deterministic_brief(input_text)
    if provider == "openai":
        return _openai_brief(input_text, model=model, max_output_tokens=max_output_tokens) or _deterministic_brief(input_text)
    # Auto: prefer Gemini if configured.
    if os.environ.get("GEMINI_API_KEY", "").strip():
        return _gemini_brief(input_text, model=model, max_output_tokens=max_output_tokens) or _deterministic_brief(input_text)
    return _openai_brief(input_text, model=model, max_output_tokens=max_output_tokens) or _deterministic_brief(input_text)
