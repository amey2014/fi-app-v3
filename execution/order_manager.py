import sys
import os
import json
from pathlib import Path
from datetime import datetime

# Windows Force-Pathing
current_file = Path(__file__).resolve()
root_dir = current_file.parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))
os.chdir(str(root_dir))

import config

class OrderManager:
    def __init__(self, ledger_filename="execution/virtual_portfolio.json"):
        self.ledger_path = Path(ledger_filename)
        self._initialize_ledger()

    def _initialize_ledger(self):
        """Creates a virtual tracking database file if it does not already exist."""
        if not self.ledger_path.exists():
            initial_state = {
                "account_summary": {
                    "starting_capital": config.VIRTUAL_STARTING_BALANCE,
                    "current_cash_balance": config.VIRTUAL_STARTING_BALANCE,
                    "blocked_collateral": 0.0,
                    "total_equity": config.VIRTUAL_STARTING_BALANCE
                },
                "active_positions": [],
                "closed_trades_history": []
            }
            with open(self.ledger_path, 'w') as f:
                json.dump(initial_state, f, indent=4)

    def _load_ledger(self) -> dict:
        with open(self.ledger_path, 'r') as f:
            return json.load(f)

    def _save_ledger(self, data: dict):
        with open(self.ledger_path, 'w') as f:
            json.dump(data, f, indent=4)

    def execute_spread_order(self, trade_proposal: dict) -> dict:
        """Processes order entry based on your configuration simulation state rules."""
        if not config.VIRTUAL_SIMULATION_MODE:
            return {"status": "SKIPPED", "message": "Simulation Mode is False. Halting Alpaca deployment API call."}

        ledger = self._load_ledger()
        summary = ledger["account_summary"]
        
        # Calculate capital impact
        cash_credit = trade_proposal["net_credit"] * 100
        required_collateral = config.SPREAD_WIDTH_POINTS * 100
        net_risk_exposure = required_collateral - cash_credit

        # Check if virtual portfolio has enough cash to hold the margin lock requirement
        if summary["current_cash_balance"] < net_risk_exposure:
            return {"status": "REJECTED", "message": "Inadequate virtual liquidity available to lock margin requirement."}

        # Deduct capital requirements and track position locally
        summary["current_cash_balance"] += cash_credit
        summary["blocked_collateral"] += required_collateral
        summary["total_equity"] = summary["current_cash_balance"] - summary["blocked_collateral"]

        new_position = {
            "trade_id": len(ledger["closed_trades_history"]) + len(ledger["active_positions"]) + 1,
            "symbol": trade_proposal["symbol"],
            "short_strike": trade_proposal["short_strike"],
            "long_strike": trade_proposal["long_strike"],
            "entry_credit_per_share": trade_proposal["net_credit"],
            "entry_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "collateral_locked": required_collateral,
            "status": "OPEN"
        }

        ledger["active_positions"].append(new_position)
        self._save_ledger(ledger)

        return {
            "status": "SIMULATED_SUCCESS",
            "message": f"Successfully opened virtual trade ID #{new_position['trade_id']} on {new_position['symbol']}.",
            "cash_collected": cash_credit,
            "margin_collateral_blocked": required_collateral
        }

if __name__ == "__main__":
    print("Testing Local Virtual Portfolio Simulation Engine...")
    manager = OrderManager()
    
    # Mocking sample entry parameters from a green-lit strategy selector candidate
    mock_trade = {
        "symbol": "AAPL",
        "short_strike": 295.0,
        "long_strike": 290.0,
        "net_credit": 0.39
    }
    
    result = manager.execute_spread_order(mock_trade)
    print(f"\n[Simulation Status]: {result['status']}")
    print(f"Server Message:      {result['message']}")
    if "cash_collected" in result:
        print(f"Virtual Cash Added:  +${result['cash_collected']} credit income")
        print(f"Virtual Margin Lock: -${result['margin_collateral_blocked']} collateral held")
