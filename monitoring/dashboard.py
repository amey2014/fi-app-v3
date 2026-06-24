import sys
import os
import json
import time
import threading
from datetime import datetime, timedelta, UTC
from pathlib import Path
from flask import Flask, jsonify, request, render_template_string

# Windows Force-Pathing Alignment
current_file = Path(__file__).resolve()
root_dir = current_file.parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))
os.chdir(str(root_dir))

import config
from alpaca.data.historical import OptionHistoricalDataClient
from alpaca.data.requests import OptionChainRequest
from alpaca.trading.enums import ContractType

app = Flask(__name__)

import logging
import traceback
logger = logging.getLogger("OptionsTradingSystem")  # shares same logger as main.py

LEDGER_PATH = Path("execution/virtual_portfolio.json")

dashboard_state = {
    "positions": [],
    "trade_log": [],
    "account": {},
    "cycle": 0,
    "last_update": None,
    "capital_limit": 5000,
    "deployed": 0,
    "win_rate": 0.0,
    "status_message": "Command Center Standby. Tracking Active Contracts."
}

def decode_osi_symbol(osi: str) -> str:
    try:
        if len(osi) < 15: return osi
        ticker = "".join([c for c in osi if c.isalpha() and c not in ['P', 'C']])
        remainder = osi[len(ticker):]
        date_obj = datetime.strptime(remainder[:6], "%y%m%d")
        return f"{ticker} {date_obj.strftime('%b %d, %Y')} ${float(remainder[7:])/1000.0:.2f} PUT"
    except: return osi

HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Premium Options Dashboard</title>
    <meta http-equiv="refresh" content="10">
    <style>
        body { font-family: monospace; background: #0f172a; color: #cbd5e1; padding: 20px; }
        h1 { color: #818cf8; margin: 0; }
        .subtitle { color: #64748b; font-size: 12px; margin-bottom: 20px; }
        .card { background: #1e293b; border: 1px solid #334155; border-radius: 12px; padding: 20px; margin: 15px 0; }
        h2 { color: #f1f5f9; font-size: 15px; margin-top: 0; margin-bottom: 15px; border-left: 4px solid #6366f1; padding-left: 10px; }
        .positive { color: #34d399; font-weight: bold; }
        .negative { color: #f87171; font-weight: bold; }
        table { width: 100%; border-collapse: collapse; }
        th { text-align: left; padding: 12px 8px; border-bottom: 2px solid #334155; color: #94a3b8; font-size: 11px; text-transform: uppercase; }
        td { padding: 12px 8px; border-bottom: 1px solid #334155; font-size: 13px; }
        button { padding: 6px 14px; border: none; border-radius: 6px; cursor: pointer; font-weight: bold; font-family: monospace; font-size: 11px; background: #ef4444; color: white; }
        .code-pill { background: #0f172a; padding: 3px 8px; border-radius: 4px; color: #fbbf24; font-size: 11px; border: 1px solid #334155; }
    </style>
</head>
<body>
    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 5px;">
        <h1>⚡ Institutional Options Command Hub</h1>
        <button style="background:#f59e0b; color:#0f172a; font-weight:bold; border-radius:6px;" onclick="fetch('/reset',{method:'POST'}).then(()=>location.reload())">⚠ Reset Ledger</button>
    </div>
    <div class="subtitle">Auto-Sync Screen Refresh: 10s | Last Check: {{ state.last_update }}</div>
    <div class="log-box" style="background: #020617; padding: 12px; border-left: 4px solid #818cf8; margin-bottom:15px;">🤖 Automation Status: {{ state.status_message }}</div>

    <div class="card">
        <div class="stat" style="display:inline-block; margin-right:40px;"><div style="font-size:11px; color:#64748b;">PORTFOLIO VALUE</div><div style="font-size:26px; font-weight:900; color:#818cf8;">${{ "{:,.2f}".format(state.account.get("total_equity", 5000.0)) }}</div></div>
        <div class="stat" style="display:inline-block; margin-right:40px;"><div style="font-size:11px; color:#64748b;">CASH AVAILABLE</div><div style="font-size:26px; font-weight:900; color:#34d399;">${{ "{:,.2f}".format(state.account.get("current_cash_balance", 5000.0)) }}</div></div>
        <div class="stat" style="display:inline-block;"><div style="font-size:11px; color:#64748b;">COLLATERAL LOCKED</div><div style="font-size:26px; font-weight:900; color:#fbbf24;">${{ "{:,.2f}".format(state.account.get("blocked_collateral", 0.0)) }}</div></div>
    </div>

    <div class="card">
        <h2>📊 Active Credit Spread Inventory (Verified Alpaca Tracking Matrix)</h2>
        {% if state.positions %}
        <table>
            <thead>
                <tr>
                    <th>Asset</th><th>Target Expiration</th><th>Spread Boundaries</th><th>Alpaca Broker Contract Code</th><th>Entry</th><th>Current</th><th>P&L %</th><th>P&L $</th><th>Action</th>
                </tr>
            </thead>
            <tbody>
                {% for p in state.positions %}
                <tr>
                    <td style="font-weight: bold; color: #fff;">📊 {{ p.symbol }}</td>
                    <td style="color: #f1f5f9; font-weight: bold;">{{ p.get('expiry_date', 'N/A') }}</td>
                    <td style="color: #38bdf8;">Short ${{ p.get('short_strike') }} / Long ${{ p.get('long_strike') }}</td>
                    <td><span class="code-pill">{{ p.get('short_leg_symbol', 'N/A') }}</span></td>
                    <td>${{ "%.2f"|format(p.get('entry_credit_per_share', 0.0)) }}</td>
                    <td style="color:#818cf8; font-weight:bold;">${{ "%.2f"|format(p.get('current_price', 0.0)) }}</td>
                    <td class="{{ 'positive' if p.get('pnl_pct',0) >= 0 else 'negative' }}">{{ "%+.1f"|format(p.get('pnl_pct',0)) }}%</td>
                    <td class="{{ 'positive' if p.get('pnl_usd',0) >= 0 else 'negative' }}">${{ "%+.2f"|format(p.get('pnl_usd',0)) }}</td>
                    <td><button onclick="fetch('/exit',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({trade_id:'{{p.trade_id}}'})}).then(()=>location.reload())">Force Close</button></td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
        {% else %}
        <p style="color:#64748b; font-style: italic;">No active positions held inside inventory ledger. Standby for time schedules matching.</p>
        {% endif %}
    </div>
</body>
</html>
"""

def sync_state_from_ledger():
    if not LEDGER_PATH.exists():
        logger.warning("[DASHBOARD:SYNC] virtual_portfolio.json not found — ledger missing")
        return
    try:
        logger.debug(f"[DASHBOARD:SYNC] Reading ledger from {LEDGER_PATH}")
        client = OptionHistoricalDataClient(api_key=config.ALPACA_API_KEY, secret_key=config.ALPACA_SECRET_KEY)

        with open(LEDGER_PATH, 'r', encoding='utf8') as f:
            ledger = json.load(f)

        account = ledger.get("account_summary", {})
        dashboard_state["account"] = account

        # ── AUDIT: log what the ledger says vs what we expect
        logger.info(f"[DASHBOARD:LEDGER] starting_capital=${account.get('starting_capital', 0):,.2f} | cash=${account.get('current_cash_balance', 0):,.2f} | collateral=${account.get('blocked_collateral', 0):,.2f} | equity=${account.get('total_equity', 0):,.2f}")

        raw_positions = ledger.get("active_positions", [])
        logger.info(f"[DASHBOARD:LEDGER] active_positions={len(raw_positions)} | closed_trades={len(ledger.get('closed_trades_history', []))}")

        for pos in raw_positions:
            short_symbol = pos.get("short_leg_symbol", "")
            long_symbol  = pos.get("long_leg_symbol", "")
            underlying_ticker = pos.get("symbol", "")

            logger.debug(f"[DASHBOARD:POSITION] Processing trade_id={pos.get('trade_id')} | symbol={underlying_ticker} | short={short_symbol} | long={long_symbol}")

            from datetime import date, timedelta
            today_dt  = date.today()
            dte_start = today_dt + timedelta(days=1)
            dte_end   = today_dt + timedelta(days=60)

            logger.debug(f"[DASHBOARD:API] OptionChainRequest | symbol={underlying_ticker} | expiration_date_gte={dte_start} | expiration_date_lte={dte_end}")

            req = OptionChainRequest(
                underlying_symbol=underlying_ticker,
                type=ContractType.PUT,
                expiration_date_gte=dte_start,
                expiration_date_lte=dte_end
            )
            chain_data = client.get_option_chain(req)

            if not chain_data:
                logger.warning(f"[DASHBOARD:API] {underlying_ticker} | EMPTY chain response")

            def get_mid(chain, symbol):
                """Returns mid price for a contract symbol, or None if unavailable."""
                if not chain or symbol not in chain:
                    logger.warning(f"[DASHBOARD:PRICE] {symbol} NOT FOUND in chain")
                    return None
                snapshot = chain[symbol]
                if not snapshot.latest_quote:
                    logger.warning(f"[DASHBOARD:PRICE] {symbol} | latest_quote is None")
                    return None
                bid = float(snapshot.latest_quote.bid_price or 0)
                ask = float(snapshot.latest_quote.ask_price or 0)
                if bid <= 0 or ask <= 0:
                    logger.warning(f"[DASHBOARD:PRICE] {symbol} | bid/ask zero")
                    return None
                mid = round((bid + ask) / 2, 2)
                logger.debug(f"[DASHBOARD:PRICE] {symbol} | bid=${bid:.2f} | ask=${ask:.2f} | mid=${mid:.2f}")
                return mid

            short_mid = get_mid(chain_data, short_symbol)
            long_mid  = get_mid(chain_data, long_symbol)

            entry         = float(pos.get("entry_credit_per_share", 0.0))
            collateral    = float(pos.get("collateral_locked", 0.0))
            max_loss      = collateral  # already in dollars

            # ── Compute current net spread value and P&L
            if short_mid is not None and long_mid is not None:
                # Net spread current value = short mid - long mid
                current_net_spread = round(short_mid - long_mid, 2)

                # If spread inverted (long > short), clamp to max loss
                if current_net_spread < 0:
                    current_net_spread = 0.0

                pnl_usd = round((entry - current_net_spread) * 100, 2)

                # Hard cap: can never lose more than collateral (defined-risk spread)
                pnl_usd = max(pnl_usd, -max_loss)

                pnl_pct = round((pnl_usd / max_loss) * 100, 1) if max_loss > 0 else 0.0
                price_is_live = True

                logger.info(f"[DASHBOARD:PNL] trade_id={pos.get('trade_id')} | {underlying_ticker} | entry=${entry:.2f} | short_mid=${short_mid:.2f} | long_mid=${long_mid:.2f} | net_spread=${current_net_spread:.2f} | pnl=${pnl_usd:+.2f} | pnl_pct={pnl_pct:+.1f}%")
            else:
                # Fallback — can't compute live P&L
                current_net_spread = entry
                pnl_usd = 0.0
                pnl_pct = 0.0
                price_is_live = False
                logger.warning(f"[DASHBOARD:PNL] trade_id={pos.get('trade_id')} | {underlying_ticker} | FALLBACK — missing live quotes, P&L shown as 0%")

            pos["current_price"] = current_net_spread
            pos["pnl_usd"]       = pnl_usd
            pos["pnl_pct"]       = pnl_pct
            logger.info(f"[DASHBOARD:PNL] trade_id={pos.get('trade_id')} | {underlying_ticker} | entry=${entry:.2f} | current=${live_price:.2f} | pnl=${pnl_usd:+.2f} | pnl_pct={pnl_pct:+.1f}% | price_is_live={live_price != entry}")

            # target_contract_key = pos.get("short_leg_symbol", pos.get("symbol", ""))
            # underlying_ticker = pos.get("symbol", "MSFT")

            # logger.debug(f"[DASHBOARD:POSITION] Processing trade_id={pos.get('trade_id')} | symbol={underlying_ticker} | contract={target_contract_key} | entry_credit=${pos.get('entry_credit_per_share', 0):.2f}")

            # from datetime import date, timedelta
            # today_dt = date.today()

            # # ── AUDIT: log exact API request parameters
            # dte_start = today_dt + timedelta(days=25)
            # dte_end   = today_dt + timedelta(days=50)
            # logger.debug(f"[DASHBOARD:API] OptionChainRequest | symbol={underlying_ticker} | expiration_date_gte={dte_start} | expiration_date_lte={dte_end} | looking_for_key={target_contract_key}")

            # req = OptionChainRequest(
            #     underlying_symbol=underlying_ticker,
            #     type=ContractType.PUT,
            #     expiration_date_gte=dte_start,   # ← FIXED here too
            #     expiration_date_lte=dte_end
            # )
            # chain_data = client.get_option_chain(req)

            # # ── AUDIT: log what came back and whether our contract was found
            # if not chain_data:
            #     logger.warning(f"[DASHBOARD:API] {underlying_ticker} | EMPTY chain response — live price unavailable for {target_contract_key}")
            # else:
            #     logger.debug(f"[DASHBOARD:API] {underlying_ticker} | chain returned {len(chain_data)} contracts | target_key_found={target_contract_key in chain_data}")

            # live_price = 0.0

            # if chain_data and target_contract_key in chain_data:
            #     snapshot = chain_data[target_contract_key]
            #     if snapshot.latest_quote:
            #         bid = float(snapshot.latest_quote.bid_price) if snapshot.latest_quote.bid_price else 0.0
            #         ask = float(snapshot.latest_quote.ask_price) if snapshot.latest_quote.ask_price else 0.0
            #         if bid > 0 and ask > 0:
            #             live_price = round((bid + ask) / 2, 2)
            #             logger.debug(f"[DASHBOARD:PRICE] {target_contract_key} | bid=${bid:.2f} | ask=${ask:.2f} | mid=${live_price:.2f}")
            #         else:
            #             logger.warning(f"[DASHBOARD:PRICE] {target_contract_key} | bid/ask both zero — cannot compute live price")
            #     else:
            #         logger.warning(f"[DASHBOARD:PRICE] {target_contract_key} | latest_quote is None in snapshot")
            # else:
            #     logger.warning(f"[DASHBOARD:PRICE] {target_contract_key} | NOT FOUND in chain — P&L will show as 0% (stale entry price used)")

            # entry = float(pos.get("entry_credit_per_share", 1.0))

            # # ── AUDIT: log the fallback so we know when P&L is unreliable
            # if live_price <= 0.0:
            #     logger.warning(f"[DASHBOARD:PNL] trade_id={pos.get('trade_id')} | live_price=0.0 — FALLING BACK to entry price ${entry:.2f} | P&L will display as 0%  ← THIS IS INACCURATE")
            #     live_price = entry

            # pnl_usd = round((entry - live_price) * 100, 2)
            # pnl_pct = round(((entry - live_price) / entry) * 100, 1) if entry > 0 else 0.0

            # pos["current_price"] = live_price
            # pos["pnl_usd"]       = pnl_usd
            # pos["pnl_pct"]       = pnl_pct

            # ── AUDIT: final P&L record for every position on every page load
            # logger.info(f"[DASHBOARD:PNL] trade_id={pos.get('trade_id')} | {underlying_ticker} | entry=${entry:.2f} | current=${live_price:.2f} | pnl=${pnl_usd:+.2f} | pnl_pct={pnl_pct:+.1f}% | price_is_live={live_price != entry}")

        dashboard_state["positions"]  = raw_positions
        dashboard_state["trade_log"]  = ledger.get("closed_trades_history", [])
        dashboard_state["last_update"] = datetime.now().strftime("%H:%M:%S")

        logger.debug(f"[DASHBOARD:SYNC] Sync complete at {dashboard_state['last_update']}")

    except json.JSONDecodeError as e:
        logger.error(f"[DASHBOARD:SYNC] virtual_portfolio.json is malformed JSON | error={e}")
        dashboard_state["status_message"] = f"Ledger JSON corrupt: {e}"
    except Exception as e:
        logger.error(f"[DASHBOARD:SYNC] Unexpected error | {type(e).__name__}: {e}")
        logger.debug(traceback.format_exc())
        dashboard_state["status_message"] = f"Live Sync Postponed: {e}"

@app.route("/")
def index():
    logger.debug(f"[DASHBOARD:REQUEST] Page load requested at {datetime.now().strftime('%H:%M:%S')}")
    sync_state_from_ledger()
    logger.debug(f"[DASHBOARD:RENDER] Rendering with {len(dashboard_state['positions'])} positions | account={dashboard_state['account']}")
    return render_template_string(HTML_TEMPLATE, state=dashboard_state)

@app.route("/reset", methods=["POST"])
def reset_ledger_api():
    logger.warning("[DASHBOARD:RESET] ⚠ LEDGER RESET TRIGGERED via dashboard button")
    if LEDGER_PATH.exists():
        os.remove(str(LEDGER_PATH))
        logger.warning("[DASHBOARD:RESET] Existing ledger deleted")
    blank = {
        "account_summary": {"starting_capital": 5000.0, "current_cash_balance": 5000.0, "blocked_collateral": 0.0, "total_equity": 5000.0},
        "active_positions": [],
        "closed_trades_history": []
    }
    with open(LEDGER_PATH, 'w') as f:
        json.dump(blank, f)
    logger.warning("[DASHBOARD:RESET] Fresh ledger written with $5,000 starting capital")
    return jsonify({"message": "Reset complete."})

@app.route("/exit", methods=["POST"])
def exit_position():
    tid = request.json.get("trade_id")
    logger.warning(f"[DASHBOARD:EXIT] Manual force-close triggered for trade_id={tid}")
    try:
        with open(LEDGER_PATH, 'r') as f:
            ledger = json.load(f)

        # Find the position
        position = next((p for p in ledger.get("active_positions", []) if str(p.get("trade_id")) == str(tid)), None)

        if not position:
            logger.warning(f"[DASHBOARD:EXIT] trade_id={tid} not found")
            return jsonify({"message": "Trade not found."}), 404

        # Use current_price from last sync if available, else entry price
        exit_credit    = position.get("current_price", position.get("entry_credit_per_share", 0.0))
        collateral     = float(position.get("collateral_locked", 0.0))
        entry_credit   = float(position.get("entry_credit_per_share", 0.0))
        exit_cash_paid = round(exit_credit * 100, 2)
        pnl_dollars    = round((entry_credit - exit_credit) * 100, 2)

        # Update account summary
        summary = ledger["account_summary"]
        summary["current_cash_balance"]   = round(summary["current_cash_balance"] - exit_cash_paid, 2)
        summary["blocked_collateral"]     = round(summary["blocked_collateral"] - collateral, 2)
        summary["total_max_loss_at_risk"] = round(summary.get("total_max_loss_at_risk", collateral) - collateral, 2)
        summary["total_equity"]           = round(summary["current_cash_balance"] - summary["blocked_collateral"], 2)

        # Move to closed history
        closed_record = dict(position)
        closed_record.update({
            "status":         "CLOSED",
            "exit_credit":    round(exit_credit, 4),
            "exit_cash_paid": exit_cash_paid,
            "pnl_dollars":    pnl_dollars,
            "close_reason":   "MANUAL_FORCE_CLOSE",
            "close_date":     datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })

        ledger["active_positions"]       = [p for p in ledger["active_positions"] if str(p.get("trade_id")) != str(tid)]
        ledger["closed_trades_history"].append(closed_record)

        # Recalculate collateral from scratch to prevent drift
        summary["blocked_collateral"]     = round(sum(p["collateral_locked"] for p in ledger["active_positions"]), 2)
        summary["total_max_loss_at_risk"] = summary["blocked_collateral"]
        summary["total_equity"]           = round(summary["current_cash_balance"] - summary["blocked_collateral"], 2)

        with open(LEDGER_PATH, 'w') as f:
            json.dump(ledger, f, indent=4)

        logger.warning(f"[DASHBOARD:EXIT] trade_id={tid} closed | pnl=${pnl_dollars:+.2f} | collateral_released=${collateral:.2f}")
        return jsonify({"message": "Closed manually.", "pnl": pnl_dollars})

    except Exception as e:
        logger.error(f"[DASHBOARD:EXIT] Failed to close trade_id={tid} | {type(e).__name__}: {e}")
        return jsonify({"message": f"Error: {e}"}), 500

# @app.route("/exit", methods=["POST"])
# def exit_position():
#     tid = request.json.get("trade_id")
#     logger.warning(f"[DASHBOARD:EXIT] Manual force-close triggered for trade_id={tid}")
#     try:
#         with open(LEDGER_PATH, 'r') as f:
#             ledger = json.load(f)
#         before = len(ledger.get("active_positions", []))
#         ledger["active_positions"] = [p for p in ledger.get("active_positions", []) if str(p.get("trade_id")) != str(tid)]
#         after = len(ledger["active_positions"])
#         with open(LEDGER_PATH, 'w') as f:
#             json.dump(ledger, f)
#         logger.warning(f"[DASHBOARD:EXIT] trade_id={tid} removed | positions_before={before} | positions_after={after}")
#         return jsonify({"message": "Closed manually."})
#     except Exception as e:
#         logger.error(f"[DASHBOARD:EXIT] Failed to close trade_id={tid} | {type(e).__name__}: {e}")
#         return jsonify({"message": f"Error: {e}"}), 500

def background_trading_orchestrator_loop():
    from main import TradingBotOrchestrator
    logger.info("[DASHBOARD:LOOP] Background trading loop thread started")
    bot = TradingBotOrchestrator()
    while True:
        try:
            now_est = datetime.now(UTC).replace(tzinfo=None) + (timedelta(hours=-4) if time.localtime().tm_isdst else timedelta(hours=-5))
            target_wake_time = now_est.replace(hour=config.MARKET_OPEN_HOUR, minute=config.MARKET_OPEN_MINUTE, second=0, microsecond=0) - timedelta(minutes=config.AUTOMATED_WAKE_BUFFER_MINUTES)

            if now_est.weekday() >= 5:
                logger.debug(f"[DASHBOARD:LOOP] Weekend — sleeping 1 hour")
                dashboard_state["status_message"] = "Weekend Mode: Markets Closed. Loop sleeping."
                time.sleep(3600)
                continue

            if now_est < target_wake_time:
                sleep_seconds = (target_wake_time - now_est).total_seconds()
                logger.info(f"[DASHBOARD:LOOP] Pre-market standby | now={now_est.strftime('%H:%M')} EST | wake={target_wake_time.strftime('%H:%M')} EST | sleep={sleep_seconds/60:.1f} mins")
                dashboard_state["status_message"] = f"Pre-Market Standby: Sleeping {sleep_seconds/60:.1f} mins until {target_wake_time.strftime('%H:%M')} EST."
                time.sleep(sleep_seconds)
                continue

            market_close_time = now_est.replace(hour=16, minute=0, second=0)
            if now_est >= market_close_time:
                tomorrow_wake = target_wake_time + timedelta(days=1)
                sleep_seconds = (tomorrow_wake - now_est).total_seconds()
                logger.info(f"[DASHBOARD:LOOP] Market closed — sleeping {sleep_seconds/3600:.2f}h until tomorrow {tomorrow_wake.strftime('%H:%M')} EST")
                dashboard_state["status_message"] = f"Market Closed. Sleeping {sleep_seconds/3600:.2f}h until tomorrow."
                time.sleep(sleep_seconds)
                continue

            dashboard_state["cycle"] += 1
            logger.info(f"[DASHBOARD:LOOP] ── Cycle #{dashboard_state['cycle']} starting at {now_est.strftime('%H:%M')} EST ──")
            dashboard_state["status_message"] = f"Cycle #{dashboard_state['cycle']}: Running active data scans..."

            bot.run_daily_scan()

            logger.info(f"[DASHBOARD:LOOP] Cycle #{dashboard_state['cycle']} completed. Next scan in 15 minutes.")
            dashboard_state["status_message"] = "Monitoring active. Next scan in 15 minutes."
            time.sleep(900)

        except Exception as e:
            logger.error(f"[DASHBOARD:LOOP] Unhandled exception in cycle #{dashboard_state['cycle']} | {type(e).__name__}: {e}")
            logger.debug(traceback.format_exc())
            dashboard_state["status_message"] = f"Loop Error: {e}"
            time.sleep(60)

if __name__ == "__main__":
    threading.Thread(target=background_trading_orchestrator_loop, daemon=True).start()
    logger.info("[DASHBOARD] Flask dashboard starting on http://127.0.0.1:5000")
    logger.info(f"[DASHBOARD] Ledger path: {LEDGER_PATH.resolve()}")
    logger.info(f"[DASHBOARD] Ledger exists: {LEDGER_PATH.exists()}")
    app.run(host="127.0.0.1", port=5000, debug=False)

