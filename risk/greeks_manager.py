import sys
import os
from pathlib import Path

# Windows Force-Pathing
current_file = Path(__file__).resolve()
root_dir = current_file.parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))
os.chdir(str(root_dir))

class RiskManager:
    def __init__(self):
        pass

    def evaluate_position_exit(self, entry_credit: float, current_market_price: float, current_dte: int) -> dict:
        """
        Enforces your colleague's exact Exit Protocol:
        EXIT at 21 DTE OR 50% profit — whichever comes first. Also caps loss at 2x credit.
        """
        # Rule 1: Time Limit Trigger (21 DTE Mandatory Escape Hatch to avoid late Gamma risk)
        if current_dte <= 21:
            return {
                "action": "CLOSE_POSITION",
                "reason": f"DTE reached threshold ({current_dte} days left). Close to eliminate escalating Gamma curve risks."
            }

        # Rule 2: 50% Profit Taker Target
        # If we collected $0.39, 50% profit is achieved if the spread price drops to $0.19 or below
        target_profit_price = entry_credit * 0.50
        if current_market_price <= target_profit_price:
            return {
                "action": "CLOSE_POSITION",
                "reason": f"Profit target achieved. Captured >= 50% of the original premium credit (Current: ${current_market_price:.2f})."
            }

        # Rule 3: Hard Stop Loss Safety (Loss exceeds 2x credit collected)
        max_loss_price = entry_credit * 3.0
        if current_market_price >= max_loss_price:
            return {
                "action": "CLOSE_POSITION",
                "reason": f"Risk threshold breached. Position loss hit maximum 2x credit allowance."
            }

        return {"action": "HOLD", "reason": "Position continues to run smoothly within standard parameters."}

if __name__ == "__main__":
    print("Testing Colleague's Risk Exit Controller Matrix...")
    risk_engine = RiskManager()

    # Scenario A: Position hits 50% profit target
    scenario_a = risk_engine.evaluate_position_exit(entry_credit=0.39, current_market_price=0.18, current_dte=32)
    print(f"\n[Profit Scenario]: {scenario_a['action']} -> {scenario_a['reason']}")

    # Scenario B: Position enters 21 DTE window
    scenario_b = risk_engine.evaluate_position_exit(entry_credit=0.39, current_market_price=0.30, current_dte=21)
    print(f"[Time DTE Scenario]: {scenario_b['action']} -> {scenario_b['reason']}")
