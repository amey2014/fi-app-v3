import sys
import os
from pathlib import Path

current_file = Path(__file__).resolve()
root_dir = current_file.parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))
os.chdir(str(root_dir))

import config

class StrategySelector:
    def __init__(self):
        pass

    def evaluate_stock_candidate(self, stock: str, data_payload: dict) -> dict:
        score = 0
        rejections = []

        iv_rank = data_payload.get("iv_rank", 0.0)
        current_price = data_payload.get("current_price", 0.0)
        ma_50 = data_payload.get("ma_50", 0.0)
        ma_200 = data_payload.get("ma_200", 0.0)
        days_to_earnings = data_payload.get("days_to_earnings", 999)
        options_liquid = data_payload.get("options_liquid", True)
        market_cap_billions = data_payload.get("market_cap_b", 500.0)

        # Filter 1: IV Rank
        if iv_rank >= config.MIN_IV_RANK:
            score += 1
        else:
            rejections.append(f"Low IV Rank ({iv_rank:.1f} < {config.MIN_IV_RANK})")

        # Filter 2: Moving Average Trend Check
        if current_price >= ma_50 or current_price >= ma_200:
            score += 1
        else:
            rejections.append(f"Downtrend (Price ${current_price:.2f} is below 50MA ${ma_50:.2f} and 200MA ${ma_200:.2f})")

        # Filter 3: Earnings
        if days_to_earnings > config.MAX_EARNINGS_LOOKAHEAD_DAYS:
            score += 1
        else:
            rejections.append(f"Approaching Earnings ({days_to_earnings} days out)")

        # Filter 4: Liquidity
        if options_liquid: score += 1
        
        # Filter 5: Quality Size
        if market_cap_billions >= 5.0 and current_price >= 10.0: score += 1

        return {
            "symbol": stock,
            "score": score,
            "status": "PASSED" if score >= 4 else "FAILED",
            "reasons_for_failure": rejections
        }

    def find_best_trade(self, candidates_list: list) -> dict:
        valid_trades = []

        print(f"\n      [DEBUG SELECTOR]: Evaluating {len(candidates_list)} final filtered assets...")
        for item in candidates_list:
            metrics = item["metrics"]
            scoring_result = self.evaluate_stock_candidate(item["symbol"], metrics)
            
            if scoring_result["status"] == "FAILED":
                print(f"      [DEBUG SELECTOR]: ❌ {item['symbol']} Failed 5-Filter Checks. Reasons: {scoring_result['reasons_for_failure']}")
                continue

            credit = item["trade_structure"]["net_credit"]
            max_loss = config.SPREAD_WIDTH_POINTS - credit
            return_on_risk = (credit / max_loss) * 100
            
            short_delta = abs(item["trade_structure"]["short_delta"])
            pop = 1.0 - short_delta

            # 🔍 PROFILING DEBUG LOG: Print precise mathematical returns check metrics
            print(f"      [DEBUG SELECTOR]: Checking yields for {item['symbol']} -> Collected: ${credit:.2f}, Margin Risked: ${max_loss:.2f}, Return on Risk: {return_on_risk:.2f}%, Target Floor: {config.MIN_RETURN_ON_RISK_PCT}%")

            if return_on_risk >= config.MIN_RETURN_ON_RISK_PCT and pop >= 0.68:
                composite_score = metrics["iv_rank"] * pop * return_on_risk
                
                valid_trades.append({
                    "symbol": item["symbol"],
                    "short_strike": item["trade_structure"]["short_strike"],
                    "long_strike": item["trade_structure"]["long_strike"],
                    "net_credit": round(credit, 2),
                    "pop_pct": round(pop * 100, 2),
                    "return_on_risk_pct": round(return_on_risk, 2),
                    "iv_rank": metrics["iv_rank"],
                    "composite_score": round(composite_score, 2)
                })

        if not valid_trades:
            return {"status": "NO_TRADES_FOUND", "message": "      [DEBUG SELECTOR]: ❌ Daily Scan Complete: No stocks passed your strict trend or premium yield hurdles."}

        # Select the single highest composite score setup
        best_trade = sorted(valid_trades, key=lambda x: x["composite_score"], reverse=True)[0]
        best_trade["status"] = "EXECUTE"
        return best_trade
