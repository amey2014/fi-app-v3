import sys
import os
import json
from pathlib import Path
from datetime import datetime
import yfinance as yf

# Windows Force-Pathing
current_file = Path(__file__).resolve()
root_dir = current_file.parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))
os.chdir(str(root_dir))

from risk.greeks_manager import RiskManager

class PortfolioUpdater:
    def __init__(self, ledger_filename="execution/virtual_portfolio.json"):
        self.ledger_path = Path(ledger_filename)
        self.risk_mgr = RiskManager()

    def update_and_clean_portfolio(self):
        """Scans live open positions, re-prices options premium, and executes defensive exits."""
        if not self.ledger_path.exists():
            print("No virtual ledger database found. Run main.py first.")
            return

        with open(self.ledger_path, 'r') as f:
            ledger = json.load(f)

        active_positions = ledger.get("active_positions", [])
        if not active_positions:
            print("\n--- Daily Status: No active virtual positions to manage. ---")
            return

        print(f"\n--- Monitoring Loop: Reviewing {len(active_positions)} Open Positions ---")
        still_active = []
        summary = ledger["account_summary"]

        for pos in active_positions:
            symbol = pos["symbol"]
            print(f"Checking live pricing for open trade ID #{pos['trade_id']} ({symbol})...")
            
            try:
                ticker = yf.Ticker(symbol)
                # Pull live current underlying price to estimate premium decay
                hist = ticker.history(period="1d")
                if hist.empty:
                    still_active.append(pos)
                    continue
                
                current_underlying = hist['Close'].iloc[-1]
                
                # Dynamic Option Premium Simulation:
                # In a live environment, this would call the Alpaca Option Chain API.
                # To simulate premium price movement for your test, we calculate how far 
                # the stock price is currently from your short strike price.
                short_strike = pos["short_strike"]
                entry_credit = pos["entry_credit_per_share"]
                
                # If stock goes up or stays flat, option decays toward $0 (Good for us)
                # If stock drops closer to or below our short strike, option premium spikes (Bad for us)
                distance_pct = (current_underlying - short_strike) / short_strike
                
                if distance_pct > 0.05:
                    # Stock moved safely away; premium decayed by 60% (Profit target hit!)
                    simulated_current_premium = entry_credit * 0.40
                elif distance_pct < -0.02:
                    # Stock crashed past strike; premium surged (Stop-loss hit)
                    simulated_current_premium = entry_credit * 3.10
                else:
                    # Stock stayed flat; normal time decay (e.g., decayed by 20%)
                    simulated_current_premium = entry_credit * 0.80

                # Simulated mock-DTE countdown tracking (assumes position was opened 5 days ago for testing)
                # In real execution, this tracks days between current date and option contract maturity date
                simulated_dte = 28 

                # Pass pricing to your Colleague's Risk Matrix Module
                exit_check = self.risk_mgr.evaluate_position_exit(
                    entry_credit=entry_credit,
                    current_market_price=simulated_current_premium,
                    current_dte=simulated_dte
                )

                if exit_check["action"] == "CLOSE_POSITION":
                    print(f"--> [EXIT TRIGGERED]: {exit_check['reason']}")
                    
                    # Financial settlement calculations
                    buyback_cost = simulated_current_premium * 100
                    collateral_returned = pos["collateral_locked"]
                    final_pnl = (entry_credit * 100) - buyback_cost
                    
                    # Adjust accounting numbers in local database balance
                    summary["current_cash_balance"] -= buyback_cost
                    summary["blocked_collateral"] -= collateral_returned
                    summary["total_equity"] = summary["current_cash_balance"] - summary["blocked_collateral"]
                    
                    # Record closed trade metrics into historical archives
                    pos["status"] = "CLOSED"
                    pos["exit_date"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    pos["exit_premium_price"] = round(simulated_current_premium, 2)
                    pos["final_trade_pnl"] = round(final_pnl, 2)
                    
                    ledger["closed_trades_history"].append(pos)
                    print(f"--> Trade settled. Finalized P&L: ${final_pnl:+.2f} cash profit locked in.")
                else:
                    print("--> [HOLD]: Position remains safely within target boundaries.")
                    still_active.append(pos)

            except Exception as e:
                print(f"Error updating pricing variables for {symbol}: {e}")
                still_active.append(pos)

        # Sync ledger arrays and save back to local JSON
        ledger["active_positions"] = still_active
        with open(self.ledger_path, 'w') as f:
            json.dump(ledger, f, indent=4)
        
        print("\n--- Account Health Summary ---")
        print(f"Total Portfolio Value: ${ledger['account_summary']['total_equity']:.2f}")
        print(f"Available Liquid Cash: ${ledger['account_summary']['current_cash_balance']:.2f}")
        print(f"Margin Safety Lockup:  ${ledger['account_summary']['blocked_collateral']:.2f}")

if __name__ == "__main__":
    updater = PortfolioUpdater()
    updater.update_and_clean_portfolio()
