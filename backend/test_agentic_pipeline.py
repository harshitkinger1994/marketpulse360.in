import json
import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import backend.agentic_pipeline as ap


class AgenticPipelineTests(unittest.TestCase):
    def test_trader_brief_llm_formatter_branch(self):
        original_provider = ap.LLM_PROVIDER
        original_generate = ap._generate_llm_text
        try:
            ap.LLM_PROVIDER = "gemini"

            def fake_generate(system_prompt, user_input, *, model=None, max_output_tokens=250):
                self.assertIn("Trader-Facing Analysis Composer for Telegram", system_prompt)
                self.assertIn('"strategy_context"', user_input)
                self.assertIn('"analysis_output"', user_input)
                self.assertIn('"derived_execution_context"', user_input)
                self.assertEqual(model, ap.TRADER_BRIEF_GEMINI_MODEL)
                return (
                    "Strategy: India F&O + Index Radar (EMA9 Growth 30)\n"
                    "Mode: NEW TRADES\n"
                    "Market: INDIA | Type: SWING / INTRADAY UNIVERSAL\n"
                    "Filters: Daily range > ATR(14) x 1.4 | Daily/Weekly EMA9 Side Alignment | Volume > SMA(14) | RSI(14) in 30.0-60.0\n\n"
                    "## BAJAJ FINANCE (BAJFINANCE) | Side: BUY\n"
                    "• Current Market Price (CMP): ₹883.00\n"
                    "• The Strategy Signal: Price is reclaiming value above VWAP and the 9 EMA, so the bullish side remains in control.\n"
                    "• The Liquidity Sweep Zone: ₹787.85 – ₹1,102.55 where stops are likely to be swept before reversal\n"
                )

            ap._generate_llm_text = fake_generate
            formatted = ap.format_single_agent_group_message(
                "INDIA | BAJFINANCE | BUY | 2026-06-01 | ₹883.00",
                {
                    "input_payload": {
                        "ticker": "BAJAJ FINANCE",
                        "live_data": {"cmp": 883.0, "rsi_14": 43.74, "volume_vs_avg": "UNAVAILABLE"},
                        "indicators": {"9_ema": 946.12, "vwap": 879.13, "daily_candle_type": "Bullish candle with controlled upper wick"},
                        "market_structure": {"weekly_candle_shape": "PRIMARY_DOWNTREND", "completed_chart_patterns": "PRIMARY_DOWNTREND"},
                        "derivatives": {"pcr": 0.91},
                        "meta": {"source_key": "BAJFINANCE"},
                    },
                    "parsed_output": {},
                },
                market="INDIA",
                strategy_context={
                    "title": "India F&O + Index Radar (EMA9 Growth 30)",
                    "id": "india_ema9_growth30_on",
                    "mode": "NEW TRADES",
                    "market": "INDIA",
                    "trade_type": "SWING / INTRADAY UNIVERSAL",
                    "selection": "all new eligible trades (no per-asset cap)",
                    "freshness": "signal age <= 7 day(s)",
                    "filters": "Daily range > ATR(14) x 1.4 | Daily/Weekly EMA9 Side Alignment | Volume > SMA(14) | RSI(14) in 30.0-60.0",
                },
            )
        finally:
            ap._generate_llm_text = original_generate
            ap.LLM_PROVIDER = original_provider

        self.assertIn("## BAJAJ FINANCE (BAJFINANCE) | Side: BUY", formatted)
        self.assertIn("The Liquidity Sweep Zone:", formatted)
        self.assertIn("Current Market Price (CMP): ₹883.00", formatted)

    def test_trader_brief_llm_incomplete_falls_back_to_derived_execution(self):
        original_provider = ap.LLM_PROVIDER
        original_generate = ap._generate_llm_text
        try:
            ap.LLM_PROVIDER = "gemini"

            def fake_generate(system_prompt, user_input, *, model=None, max_output_tokens=250):
                return (
                    "Strategy: India F&O + Index Radar (EMA9 Growth 30)\n"
                    "Mode: NEW TRADES\n"
                    "Market: INDIA | Type: SWING / INTRADAY UNIVERSAL\n\n"
                    "## BAJAJ FINANCE (BAJFINANCE) | Side: BUY\n"
                    "• Current Market Price (CMP): ₹883.00\n"
                    "• The Strategy Signal: The market structure is messy and chop-heavy.\n"
                    "🎯 Dhan Super Order Terminal Execution Parameters\n"
                    "• Entry / Trigger Price: N/A\n"
                    "• Strict Stop-Loss (The Capital Shield): N/A (Risk: N/A)\n"
                    "• Super Order Target Leg 1 (Book 50%): N/A (Potential Gain: N/A)\n"
                    "• Super Order Target Leg 2 (Runway Exit): N/A (Potential Gain: N/A)\n"
                )

            ap._generate_llm_text = fake_generate
            result = ap.run_single_agent_quant_terminal("SBIN")
            formatted = ap.format_single_agent_group_message(
                "INDIA | SBIN | BUY | 2026-06-01 | ₹615.00",
                result,
                market="INDIA",
                strategy_context={
                    "title": "India F&O + Index Radar (EMA9 Growth 30)",
                    "id": "india_ema9_growth30_on",
                    "mode": "NEW TRADES",
                    "market": "INDIA",
                    "trade_type": "SWING / INTRADAY UNIVERSAL",
                    "selection": "all new eligible trades (no per-asset cap)",
                    "freshness": "signal age <= 7 day(s)",
                    "filters": "Daily range > ATR(14) x 1.4 | Daily/Weekly EMA9 Side Alignment | Volume > SMA(14) | RSI(14) in 30.0-60.0",
                },
            )
        finally:
            ap._generate_llm_text = original_generate
            ap.LLM_PROVIDER = original_provider

        self.assertIn("Entry / Trigger Price:", formatted)
        self.assertNotIn("Entry / Trigger Price: N/A", formatted)
        self.assertNotIn("Strict Stop-Loss (The Capital Shield): N/A", formatted)
        self.assertNotIn("Super Order Target Leg 1 (Book 50%): N/A", formatted)
        self.assertNotIn("Super Order Target Leg 2 (Runway Exit): N/A", formatted)
        self.assertIn("Timeframe Alignment:", formatted)

    def test_single_agent_terminal_pipeline(self):
        calls = []

        def fake_generate(system_prompt, user_input, *, model=None, max_output_tokens=250):
            calls.append((system_prompt, user_input))
            if "SINGLE-AGENT END-TO-END DHAN TERMINAL ARCHITECT" in system_prompt:
                self.assertIn('"ticker": "SBIN"', user_input)
                self.assertIn('"live_data"', user_input)
                self.assertIn('"market_structure"', user_input)
            else:
                self.assertIn("Trader-Facing Analysis Composer for Telegram", system_prompt)
                self.assertIn('"derived_execution_context"', user_input)
                return (
                    "Strategy: India F&O + Index Radar (EMA9 Growth 30)\n"
                    "Mode: NEW TRADES\n"
                    "Market: INDIA | Type: SWING / INTRADAY UNIVERSAL\n\n"
                    "## SBIN | Side: BUY\n"
                    "• Current Market Price (CMP): ₹615.00\n"
                    "🎯 Dhan Super Order Terminal Execution Parameters\n"
                    "• Entry / Trigger Price: N/A\n"
                    "• Strict Stop-Loss (The Capital Shield): N/A (Risk: N/A)\n"
                    "• Super Order Target Leg 1 (Book 50%): N/A (Potential Gain: N/A)\n"
                    "• Super Order Target Leg 2 (Runway Exit): N/A (Potential Gain: N/A)\n"
                )
            return json.dumps(
                {
                    "agent_1_directional_alpha_filter": {
                        "ticker": "SBIN",
                        "side": "BUY",
                        "strategy_signal_validation": "Price is aligned with the EMA and VWAP stack.",
                    },
                    "agent_2_liquidity_pool_sweep_quant": {
                        "ticker": "SBIN",
                        "upper_side_sweep_level": 625.0,
                        "lower_side_sweep_level": 610.0,
                        "sweep_trap_validation": "Sweep bands line up with nearby liquidity voids.",
                    },
                    "agent_3_combined_resistance_call_barrier": {
                        "ticker": "SBIN",
                        "strongest_u_turn_r": 625.0,
                        "r_max_oi_strike": "625CE",
                        "r_max_oi_volume": "4.5M contracts resting",
                        "major_resistance_range_chart": "615.0 - 628.0",
                        "why_it_u_turns_r": "The 625 strike caps upside through a heavy call wall.",
                    },
                    "agent_4_combined_support_order_flow": {
                        "ticker": "SBIN",
                        "strongest_u_turn_s": 605.0,
                        "s_max_oi_strike": "605PE",
                        "s_max_oi_volume": "6.2M contracts resting",
                        "pcr_ratio": 1.08,
                        "major_support_range_chart": "603.0 - 612.0",
                        "why_it_u_turns_s": "The 605 strike absorbs downside through a strong put cushion.",
                        "oi_shifting_verdict": "Put writers are defending while call writers retreat.",
                    },
                    "agent_5_tactical_entry_range_architect": {
                        "ticker": "SBIN",
                        "order_type": "BUY LIMIT",
                        "execution_entry_range_1_2_days": "₹612.0 – ₹616.0",
                        "terminal_entry_rationale": "The entry window aligns with a retest of the support shelf.",
                    },
                    "agent_6_dhan_super_order_terminal_architect": {
                        "ticker": "SBIN",
                        "dhan_product_type": "SUPER_ORDER",
                        "dhan_transaction_type": "BUY LIMIT",
                        "limit_entry_price": 615.0,
                        "stop_loss_price": 609.9,
                        "stop_loss_percentage": "0.83%",
                        "target_1_price": 628.0,
                        "target_1_percentage": "2.11%",
                        "target_2_price": 625.05,
                        "target_2_percentage": "1.65%",
                        "calculated_risk_reward_ratio": "1:2.80",
                        "dhan_order_placement_rationale": "BUY limit aligned to the entry range and support shelf.",
                    },
                }
            )

        original = ap._generate_llm_text
        try:
            ap._generate_llm_text = fake_generate
            result = ap.run_single_agent_quant_terminal("SBIN")
            formatted = ap.format_single_agent_group_message(
                "INDIA | SBIN | BUY | 2026-06-01 | ₹615.00",
                result,
                market="INDIA",
                strategy_context={
                    "title": "India F&O + Index Radar (EMA9 Growth 30)",
                    "id": "india_ema9_growth30_on",
                    "mode": "NEW TRADES",
                    "market": "INDIA",
                    "trade_type": "SWING / INTRADAY UNIVERSAL",
                    "selection": "all new eligible trades (no per-asset cap)",
                    "freshness": "signal age <= 7 day(s)",
                    "filters": "Daily range > ATR(14) x 1.4 | Daily/Weekly EMA9 Side Alignment | Volume > SMA(14) | RSI(14) in 30.0-60.0",
                },
            )
        finally:
            ap._generate_llm_text = original

        self.assertEqual(result["ticker"], "SBIN")
        self.assertEqual(len(calls), 2)
        self.assertEqual(result["parsed_output"]["agent_1_directional_alpha_filter"]["side"], "BUY")
        self.assertIn("Strategy: India F&O + Index Radar (EMA9 Growth 30)", formatted)
        self.assertIn("## SBIN | Side: BUY", formatted)
        self.assertIn("Current Market Price (CMP):", formatted)
        self.assertIn("Strongest 99% U-Turn Resistance (R):", formatted)
        self.assertIn("Timeframe Alignment:", formatted)
        self.assertIn("Super Order Target Leg 2 (Runway Exit):", formatted)
        self.assertNotIn("Trade Management Protocol", formatted)
        self.assertNotIn('"dhan_transaction_type"', formatted)

    def test_single_agent_neutral_short_circuit_shape(self):
        def fake_generate(system_prompt, user_input, *, model=None, max_output_tokens=250):
            return json.dumps(
                {
                    "agent_1_directional_alpha_filter": {
                        "ticker": "SBIN",
                        "side": "NEUTRAL",
                        "strategy_signal_validation": "Price is too messy to validate a structural edge.",
                    },
                    "agent_2_liquidity_pool_sweep_quant": {
                        "ticker": "SBIN",
                        "upper_side_sweep_level": 620.5,
                        "lower_side_sweep_level": 610.0,
                        "sweep_trap_validation": "The sweep zones align with nearby liquidity voids where trapped stops typically cluster.",
                    },
                    "agent_3_combined_resistance_call_barrier": {
                        "ticker": "SBIN",
                        "strongest_u_turn_r": 625.0,
                        "r_max_oi_strike": "625CE",
                        "r_max_oi_volume": "185000",
                        "major_resistance_range_chart": "615.0 - 628.0",
                        "why_it_u_turns_r": "The 625 strike has the heaviest call wall and aligns with the prior weekly wick extreme, so upside is likely to stall there.",
                    },
                    "agent_4_combined_support_order_flow": {
                        "ticker": "SBIN",
                        "strongest_u_turn_s": 605.0,
                        "s_max_oi_strike": "605PE",
                        "s_max_oi_volume": "142000",
                        "pcr_ratio": 1.08,
                        "major_support_range_chart": "603.0 - 612.0",
                        "why_it_u_turns_s": "The 605 strike has the deepest put wall and matches the weekly hammer low, so demand should defend the floor.",
                        "oi_shifting_verdict": "Put writers are defending while call writers retreat, keeping price engineered toward the upside until support breaks.",
                    },
                    "agent_5_tactical_entry_range_architect": {
                        "ticker": "SBIN",
                        "order_type": "NO TRADE",
                        "execution_entry_range_1_2_days": "",
                        "terminal_entry_rationale": "Agent 1 flagged neutral structure, so no execution window is opened.",
                    },
                    "agent_6_dhan_super_order_terminal_architect": {
                        "ticker": "SBIN",
                        "dhan_product_type": "SUPER_ORDER",
                        "dhan_transaction_type": "NO TRADE",
                        "limit_entry_price": 0.0,
                        "stop_loss_price": 0.0,
                        "stop_loss_percentage": "0.00%",
                        "target_1_price": 0.0,
                        "target_1_percentage": "0.00%",
                        "target_2_price": 0.0,
                        "target_2_percentage": "0.00%",
                        "calculated_risk_reward_ratio": "NA",
                        "dhan_order_placement_rationale": "Agent 1 and Agent 5 are aligned to no-trade mode, so the terminal order block is intentionally suppressed.",
                    },
                }
            )

        original = ap._generate_llm_text
        try:
            ap._generate_llm_text = fake_generate
            result = ap.run_single_agent_quant_terminal("SBIN")
        finally:
            ap._generate_llm_text = original

        self.assertEqual(result["parsed_output"]["agent_1_directional_alpha_filter"]["side"], "NEUTRAL")
        self.assertEqual(result["parsed_output"]["agent_5_tactical_entry_range_architect"]["order_type"], "NO TRADE")
        self.assertEqual(
            result["parsed_output"]["agent_6_dhan_super_order_terminal_architect"]["dhan_transaction_type"],
            "NO TRADE",
        )


if __name__ == "__main__":
    unittest.main()
