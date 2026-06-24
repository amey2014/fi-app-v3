import sys
import os
import json
import logging
from pathlib import Path
from datetime import datetime

# Windows Force-Pathing
current_file = Path(__file__).resolve()
root_dir     = current_file.parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))
os.chdir(str(root_dir))

import config

logger = logging.getLogger("OptionsTradingSystem")


class OrderManager:

    def __init__(self, ledger_filename="execution/virtual_portfolio.json"):
        self.ledger_path = Path(ledger_filename)
        self._initialize_ledger()
        logger.info(f"[ORDER_MGR] Initialised | ledger={self.ledger_path.resolve()} | simulation={config.VIRTUAL_SIMULATION_MODE}")

    def _initialize_ledger(self):
        """Creates a fresh virtual tracking ledger if one does not already exist."""
        if not self.ledger_path.exists():
            initial_state = {
                "account_summary": {
                    "starting_capital":       config.VIRTUAL_STARTING_BALANCE,
                    "current_cash_balance":   config.VIRTUAL_STARTING_BALANCE,
                    "blocked_collateral":     0.0,
                    "total_max_loss_at_risk": 0.0,
                    "total_equity":           config.VIRTUAL_STARTING_BALANCE
                },
                "next_trade_id": 1,
                "active_positions":      [],
                "closed_trades_history": []
            }
            with open(self.ledger_path, 'w') as f:
                json.dump(initial_state, f, indent=4)
            logger.info(f"[ORDER_MGR] Fresh ledger created at {self.ledger_path}")

    def _load_ledger(self) -> dict:
        try:
            with open(self.ledger_path, 'r') as f:
                return json.load(f)
        except json.JSONDecodeError as e:
            logger.error(f"[ORDER_MGR] Ledger JSON is corrupted | error={e}")
            raise
        except FileNotFoundError:
            logger.error(f"[ORDER_MGR] Ledger file not found at {self.ledger_path}")
            raise

    def _save_ledger(self, data: dict):
        try:
            with open(self.ledger_path, 'w') as f:
                json.dump(data, f, indent=4)
            logger.debug(f"[ORDER_MGR] Ledger saved successfully")
        except Exception as e:
            logger.error(f"[ORDER_MGR] Failed to save ledger | error={e}")
            raise

    def execute_spread_order(self, trade_proposal: dict) -> dict:
        """
        Logs a virtual spread entry to the ledger.
        Never places any order on Alpaca.
        """
        symbol = trade_proposal.get("symbol", "UNKNOWN")

        logger.info(f"[ORDER_MGR] execute_spread_order() called for {symbol}")
        logger.debug(f"[ORDER_MGR] Full trade_proposal received: {trade_proposal}")

        # ── Guard 1: simulation mode check
        if not config.VIRTUAL_SIMULATION_MODE:
            logger.warning(f"[ORDER_MGR] SIMULATION_MODE=False — real order placement not implemented. Halting.")
            return {"status": "SKIPPED", "message": "Simulation Mode is False. Real order placement not implemented."}

        ledger  = self._load_ledger()
        summary = ledger["account_summary"]

        # ── Guard 2: duplicate position check
        open_symbols = [p["symbol"] for p in ledger["active_positions"]]
        if symbol in open_symbols:
            logger.warning(f"[ORDER_MGR] REJECTED {symbol} — already have an open position in this symbol")
            return {"status": "REJECTED", "message": f"Duplicate position: {symbol} already open."}

        # ── Guard 3: max positions check
        if len(ledger["active_positions"]) >= config.MAX_OPEN_SPREADS:
            logger.warning(f"[ORDER_MGR] REJECTED — max open spreads reached ({config.MAX_OPEN_SPREADS})")
            return {"status": "REJECTED", "message": f"Max open spreads ({config.MAX_OPEN_SPREADS}) already reached."}

        # ── Capital calculations
        net_credit         = float(trade_proposal["net_credit"])
        spread_width       = float(trade_proposal.get("spread_width", config.SPREAD_WIDTH_POINTS))
        cash_credit        = round(net_credit * 100, 2)
        # Correct collateral = max loss = (width - credit) × 100
        max_loss_per_spread = round((spread_width - net_credit) * 100, 2)

        logger.debug(f"[ORDER_MGR] Capital check | net_credit=${net_credit} | spread_width=${spread_width} | cash_credit=${cash_credit} | max_loss=${max_loss_per_spread}")

        # ── Guard 4: sufficient capital check
        available_cash = summary["current_cash_balance"] - summary["blocked_collateral"]
        if available_cash < max_loss_per_spread:
            logger.warning(f"[ORDER_MGR] REJECTED {symbol} — insufficient capital | available=${available_cash:.2f} | required=${max_loss_per_spread:.2f}")
            return {"status": "REJECTED", "message": f"Insufficient capital. Need ${max_loss_per_spread:.2f}, have ${available_cash:.2f}."}

        # ── Update account summary
        summary["current_cash_balance"]   = round(summary["current_cash_balance"] + cash_credit, 2)
        summary["blocked_collateral"]     = round(summary["blocked_collateral"] + max_loss_per_spread, 2)
        summary["total_max_loss_at_risk"] = round(summary.get("total_max_loss_at_risk", 0) + max_loss_per_spread, 2)
        # True equity = starting capital minus total max loss exposure across all open positions
        summary["total_equity"] = round(summary["current_cash_balance"] - summary["blocked_collateral"], 2)

        logger.debug(f"[ORDER_MGR] Updated account | cash=${summary['current_cash_balance']} | collateral=${summary['blocked_collateral']} | equity=${summary['total_equity']}")

        # ── Build complete position record
        trade_id = ledger.get("next_trade_id", 1)
        ledger["next_trade_id"] = trade_id + 1

        new_position = {
            "trade_id":               trade_id,
            "symbol":                 symbol,

            # ── Spread structure
            "short_strike":           trade_proposal["short_strike"],
            "long_strike":            trade_proposal["long_strike"],
            "spread_width":           spread_width,

            # ── Contract identifiers (fixes N/A on dashboard)
            "short_leg_symbol":       trade_proposal.get("short_leg_symbol", ""),
            "long_leg_symbol":        trade_proposal.get("long_leg_symbol", ""),
            "expiry_date":            trade_proposal.get("expiry_date", ""),

            # ── Entry metrics
            "entry_credit_per_share": net_credit,
            "entry_cash_collected":   cash_credit,
            "short_delta":            trade_proposal.get("short_delta", 0.0),
            "iv_rank_at_entry":       trade_proposal.get("iv_rank", 0.0),

            # ── Risk metrics
            "collateral_locked":      max_loss_per_spread,
            "max_loss":               max_loss_per_spread,
            "return_on_risk_pct":     trade_proposal.get("return_on_risk_pct", 0.0),

            # ── Exit targets (for exit monitor to use)
            "profit_target":          round(net_credit * (1 - config.PROFIT_TARGET_PCT), 4),
            "exit_at_dte":            config.DTE_EXIT_THRESHOLD,

            # ── Timestamps and status
            "entry_date":             datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "status":                 "OPEN"
        }

        ledger["active_positions"].append(new_position)
        self._save_ledger(ledger)

        logger.info(f"[ORDER_MGR] ✅ POSITION OPENED | trade_id={trade_id} | {symbol} | short={trade_proposal['short_strike']} / long={trade_proposal['long_strike']} | expiry={new_position['expiry_date']} | credit=${net_credit} | collateral=${max_loss_per_spread}")
        logger.info(f"[ORDER_MGR] Contract codes | short={new_position['short_leg_symbol']} | long={new_position['long_leg_symbol']}")

        return {
            "status":                    "SIMULATED_SUCCESS",
            "message":                   f"Virtual trade #{trade_id} opened on {symbol}.",
            "trade_id":                  trade_id,
            "cash_collected":            cash_credit,
            "margin_collateral_blocked": max_loss_per_spread
        }

    def close_position(self, trade_id: int, exit_credit: float, reason: str = "MANUAL") -> dict:
        """
        Closes an open position, moves it to history, releases collateral, and logs P&L.
        exit_credit: the current net spread price to buy back at (per share)
        """
        ledger  = self._load_ledger()
        summary = ledger["account_summary"]

        # Find the position
        position = next((p for p in ledger["active_positions"] if p["trade_id"] == trade_id), None)
        if not position:
            logger.warning(f"[ORDER_MGR] close_position() — trade_id={trade_id} not found in active positions")
            return {"status": "ERROR", "message": f"Trade ID {trade_id} not found."}

        symbol              = position["symbol"]
        entry_credit        = position["entry_credit_per_share"]
        collateral          = position["collateral_locked"]
        exit_cash_paid      = round(exit_credit * 100, 2)

        # P&L: credit collected at entry minus cost to close
        pnl_dollars         = round((entry_credit - exit_credit) * 100, 2)
        pnl_pct             = round((pnl_dollars / collateral) * 100, 2)

        # Release collateral, deduct buyback cost from cash
        summary["current_cash_balance"]   = round(summary["current_cash_balance"] - exit_cash_paid, 2)
        summary["blocked_collateral"]     = round(summary["blocked_collateral"] - collateral, 2)
        summary["total_max_loss_at_risk"] = round(summary["total_max_loss_at_risk"] - collateral, 2)
        summary["total_equity"]           = round(summary["current_cash_balance"] - summary["blocked_collateral"], 2)

        # Build closed trade record
        closed_record = dict(position)
        closed_record.update({
            "status":           "CLOSED",
            "exit_credit":      round(exit_credit, 4),
            "exit_cash_paid":   exit_cash_paid,
            "pnl_dollars":      pnl_dollars,
            "pnl_pct":          pnl_pct,
            "close_reason":     reason,
            "close_date":       datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })

        # Move from active to closed
        ledger["active_positions"]      = [p for p in ledger["active_positions"] if p["trade_id"] != trade_id]
        ledger["closed_trades_history"].append(closed_record)

        # Recalculate collateral from scratch to prevent drift
        summary["total_max_loss_at_risk"] = round(sum(p["collateral_locked"] for p in ledger["active_positions"]), 2)
        summary["blocked_collateral"]     = summary["total_max_loss_at_risk"]

        self._save_ledger(ledger)

        logger.info(f"[ORDER_MGR] ✅ POSITION CLOSED | trade_id={trade_id} | {symbol} | P&L=${pnl_dollars} ({pnl_pct}%) | reason={reason}")

        return {
            "status":       "CLOSED",
            "trade_id":     trade_id,
            "symbol":       symbol,
            "pnl_dollars":  pnl_dollars,
            "pnl_pct":      pnl_pct,
            "close_reason": reason,
        }

if __name__ == "__main__":
    print("Testing Local Virtual Portfolio Simulation Engine...")
    manager = OrderManager()

    mock_trade = {
        "symbol":          "AAPL",
        "short_strike":    295.0,
        "long_strike":     290.0,
        "spread_width":    5.0,
        "net_credit":      0.39,
        "short_leg_symbol": "AAPL260718P00295000",
        "long_leg_symbol":  "AAPL260718P00290000",
        "expiry_date":     "2026-07-18",
        "short_delta":     -0.248,
        "iv_rank":         61.0,
        "return_on_risk_pct": 8.5
    }

    result = manager.execute_spread_order(mock_trade)
    print(f"\n[Simulation Status]: {result['status']}")
    print(f"Server Message: {result['message']}")
    if "cash_collected" in result:
        print(f"Virtual Cash Added:  +${result['cash_collected']}")
        print(f"Collateral Locked:   -${result['margin_collateral_blocked']}")