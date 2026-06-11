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
    if not LEDGER_PATH.exists(): return
    try:
        client = OptionHistoricalDataClient(api_key=config.ALPACA_API_KEY, secret_key=config.ALPACA_SECRET_KEY)
        with open(LEDGER_PATH, 'r', encoding='utf8') as f: ledger = json.load(f)
            
        dashboard_state["account"] = ledger.get("account_summary", {})
        raw_positions = ledger.get("active_positions", [])
        
        for pos in raw_positions:
            target_contract_key = pos.get("short_leg_symbol", pos.get("symbol", ""))
            underlying_ticker = pos.get("symbol", "MSFT")
            
            from datetime import date, timedelta
            today_dt = date.today()
            req = OptionChainRequest(underlying_symbol=underlying_ticker, type=ContractType.PUT, expiration_date_start=today_dt + timedelta(days=25), expiration_date_end=today_dt + timedelta(days=50))

            chain_data = client.get_option_chain(req)
            live_price = 0.0
            
            if chain_data and target_contract_key in chain_data:
                snapshot = chain_data[target_contract_key]
                if snapshot.latest_quote:
                    bid = float(snapshot.latest_quote.bid_price) if snapshot.latest_quote.bid_price else 0.0
                    ask = float(snapshot.latest_quote.ask_price) if snapshot.latest_quote.ask_price else 0.0
                    if bid > 0 and ask > 0: live_price = round((bid + ask) / 2, 2)

            entry = float(pos.get("entry_credit_per_share", 1.0))
            if live_price <= 0.0: live_price = entry  
                
            pos["current_price"] = live_price
            pos["pnl_usd"] = round((entry - live_price) * 100, 2)
            pos["pnl_pct"] = round(((entry - live_price) / entry) * 100, 1) if entry > 0 else 0.0
            
        dashboard_state["positions"] = raw_positions
        dashboard_state["trade_log"] = ledger.get("closed_trades_history", [])
        dashboard_state["last_update"] = datetime.now().strftime("%H:%M:%S")
    except Exception as e:
        dashboard_state["status_message"] = f"Live Sync Postponed: {e}"

@app.route("/")
def index(): sync_state_from_ledger(); return render_template_string(HTML_TEMPLATE, state=dashboard_state)

@app.route("/reset", methods=["POST"])
def reset_ledger_api():
    if LEDGER_PATH.exists(): os.remove(str(LEDGER_PATH))
    with open(LEDGER_PATH, 'w') as f: json.dump({"account_summary":{"starting_capital":5000.0,"current_cash_balance":5000.0,"blocked_collateral":0.0,"total_equity":5000.0},"active_positions":[],"closed_trades_history":[]}, f)
    return jsonify({"message": "Reset complete."})

@app.route("/exit", methods=["POST"])
def exit_position():
    tid = request.json.get("trade_id")
    with open(LEDGER_PATH, 'r') as f: ledger = json.load(f)
    ledger["active_positions"] = [p for p in ledger.get("active_positions", []) if str(p.get("trade_id")) != str(tid)]
    with open(LEDGER_PATH, 'w') as f: json.dump(ledger, f)
    return jsonify({"message": "Closed manually."})

def background_trading_orchestrator_loop():
    from main import TradingBotOrchestrator
    # logger = logging.getLogger("OptionsTradingSystem")
    bot = TradingBotOrchestrator()
    
    while True:
        try:
            now_est = datetime.now(UTC).replace(tzinfo=None) + (timedelta(hours=-4) if time.localtime().tm_isdst else timedelta(hours=-5))
            target_wake_time = now_est.replace(hour=config.MARKET_OPEN_HOUR, minute=config.MARKET_OPEN_MINUTE, second=0, microsecond=0) - timedelta(minutes=config.AUTOMATED_WAKE_BUFFER_MINUTES)

            if now_est.weekday() >= 5:
                dashboard_state["status_message"] = "Weekend Mode: Markets Closed. Loop sleeping."
                time.sleep(3600); continue

            if now_est < target_wake_time:
                sleep_seconds = (target_wake_time - now_est).total_seconds()
                dashboard_state["status_message"] = f"Pre-Market Standby: Sleeping for {sleep_seconds/60:.1f} minutes until 9:00 AM EST."
                time.sleep(sleep_seconds); continue

            market_close_time = now_est.replace(hour=16, minute=0, second=0)
            if now_est >= market_close_time:
                tomorrow_wake = target_wake_time + timedelta(days=1)
                sleep_seconds = (tomorrow_wake - now_est).total_seconds()
                dashboard_state["status_message"] = f"Market Closed for the day. Sleeping for {sleep_seconds/3600:.2f} hours until tomorrow morning."
                time.sleep(sleep_seconds); continue

            dashboard_state["cycle"] += 1
            dashboard_state["status_message"] = f"Cycle #{dashboard_state['cycle']}: Running active data scans..."
            bot.run_daily_scan()
            dashboard_state["status_message"] = "Continuous Monitoring active. Next scan scheduled in 15 minutes."
            time.sleep(900)
        except Exception as e:
            dashboard_state["status_message"] = f"Loop Error: {e}"
            time.sleep(60)

if __name__ == "__main__":
    threading.Thread(target=background_trading_orchestrator_loop, daemon=True).start()
    app.run(host="127.0.0.1", port=5000, debug=False)


# import sys
# import os
# import json
# import time
# import threading
# from datetime import datetime, timedelta, UTC
# from pathlib import Path
# from flask import Flask, jsonify, request, render_template_string

# # Windows Force-Pathing Alignment
# current_file = Path(__file__).resolve()
# root_dir = current_file.parent.parent
# if str(root_dir) not in sys.path:
#     sys.path.insert(0, str(root_dir))
# os.chdir(str(root_dir))

# import config
# from alpaca.data.historical import OptionHistoricalDataClient
# from alpaca.data.requests import OptionChainRequest

# app = Flask(__name__)
# LEDGER_PATH = Path("execution/virtual_portfolio.json")

# dashboard_state = {
#     "positions": [],
#     "trade_log": [],
#     "account": {},
#     "cycle": 0,
#     "last_update": None,
#     "capital_limit": 5000,
#     "deployed": 0,
#     "win_rate": 0.0,
#     "status_message": "Command Center Online. Tracking Live Contracts."
# }

# def parse_osi_details(osi: str) -> dict:
#     """Parses a raw broker contract symbol into precise tracking parameters."""
#     try:
#         if len(osi) < 15:
#             return {"exp": "N/A", "strike": "N/A", "type": "N/A", "symbol": osi}
#         ticker = "".join([c for c in osi if c.isalpha() and c not in ['P', 'C']])
#         remainder = osi[len(ticker):]
#         date_obj = datetime.strptime(remainder[:6], "%y%m%d")
#         formatted_date = date_obj.strftime("%b %d, %Y")
#         strike_price = float(remainder[7:]) / 1000.0
#         type_word = "Put" if "P" in remainder[:2] or (len(remainder) >= 7 and remainder[6] == 'P') else "Call"
#         return {"exp": formatted_date, "strike": f"${strike_price:.2f}", "type": type_word, "symbol": osi}
#     except Exception:
#         return {"exp": "N/A", "strike": "N/A", "type": "N/A", "symbol": osi}

# HTML_TEMPLATE = """
# <!DOCTYPE html>
# <html>
# <head>
#     <title>FI-APP-V3 Premium Command Console</title>
#     <meta http-equiv="refresh" content="10">
#     <style>
#         body { font-family: monospace; background: #0f172a; color: #cbd5e1; padding: 20px; }
#         h1 { color: #818cf8; margin: 0; }
#         .subtitle { color: #64748b; font-size: 12px; margin-bottom: 20px; }
#         .card { background: #1e293b; border: 1px solid #334155; border-radius: 12px; padding: 20px; margin: 15px 0; }
#         h2 { color: #f1f5f9; font-size: 15px; margin-top: 0; margin-bottom: 15px; border-left: 4px solid #6366f1; padding-left: 10px; }
#         .positive { color: #34d399; font-weight: bold; }
#         .negative { color: #f87171; font-weight: bold; }
#         table { width: 100%; border-collapse: collapse; }
#         th { text-align: left; padding: 12px 8px; border-bottom: 2px solid #334155; color: #94a3b8; font-size: 11px; text-transform: uppercase; }
#         td { padding: 12px 8px; border-bottom: 1px solid #334155; font-size: 13px; }
#         button { padding: 6px 14px; border: none; border-radius: 6px; cursor: pointer; font-weight: bold; font-family: monospace; font-size: 11px; background: #ef4444; color: white; }
#         button:hover { background: #dc2626; }
#         .stat { display: inline-block; margin: 5px 40px 5px 0; min-width: 140px; }
#         .stat-value { font-size: 26px; font-weight: 900; margin-top: 4px; }
#         .stat-label { font-size: 11px; color: #64748b; text-transform: uppercase; font-weight: bold; }
#         .log-box { background: #020617; border: 1px solid #1e293b; border-radius: 8px; padding: 12px; font-size: 12px; color: #38bdf8; margin: 15px 0; border-left: 4px solid #818cf8; }
#         .code-pill { background: #0f172a; padding: 3px 8px; border-radius: 4px; color: #fbbf24; font-size: 11px; border: 1px solid #334155; }
#     </style>
# </head>
# <body>
#     <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 5px;">
#         <h1>⚡ Institutional Options Command Console</h1>
#         <button style="padding:6px 12px; background:#f59e0b; color: #0f172a; font-weight:bold; border-radius:6px; font-family:monospace; font-size:11px; cursor:pointer;" onclick="fetch('/reset',{method:'POST'}).then(()=>location.reload())">⚠ Reset Ledger</button>
#     </div>
#     <div class="subtitle">Auto-Sync Loop: 10s | Last Screen Refresh: {{ state.last_update }}</div>
#     <div class="log-box">🤖 Automation Status: {{ state.status_message }}</div>

#     <div class="card">
#         <div class="stat"><div class="stat-label">Portfolio Value</div><div class="stat-value" style="color: #818cf8;">${{ "{:,.2f}".format(state.account.get("total_equity", 5000.0)) }}</div></div>
#         <div class="stat"><div class="stat-label">Cash Available</div><div class="stat-value" style="color: #34d399;">${{ "{:,.2f}".format(state.account.get("current_cash_balance", 5000.0)) }}</div></div>
#         <div class="stat"><div class="stat-label">Collateral Locked</div><div class="stat-value" style="color: #fbbf24;">${{ "{:,.2f}".format(state.account.get("blocked_collateral", 0.0)) }}</div></div>
#         <div class="stat"><div class="stat-label">System Win Rate</div><div class="stat-value" style="color: #f1f5f9;">{{ state.get("win_rate", 0.0) }}%</div></div>
#     </div>

#     <div class="card">
#         <h2>📊 Active Credit Spread Inventory (Verified Alpaca Tracking Matrix)</h2>
#         {% if state.positions %}
#         <table>
#             <thead>
#                 <tr>
#                     <th>Asset</th>
#                     <th>Target Expiration</th>
#                     <th>Spread Boundaries</th>
#                     <th>Alpaca Broker Contract Code</th>
#                     <th>Entry</th>
#                     <th>Current</th>
#                     <th>P&L %</th>
#                     <th>P&L $</th>
#                     <th>Action</th>
#                 </tr>
#             </thead>
#             <tbody>
#                 {% for p in state.positions %}
#                 <tr>
#                     <td style="font-weight: bold; color: #fff; font-size: 15px;">📊 {{ p.symbol }}</td>
#                     <td style="color: #f1f5f9; font-weight: bold;">{{ p.details.exp }}</td>
#                     <td style="color: #38bdf8;">Short {{ p.details.strike }} / Long ${{ "%.2f"|format(p.long_strike) }}</td>
#                     <td><span class="code-pill">{{ p.short_leg_symbol }}</span></td>
#                     <td>${{ "%.2f"|format(p.entry_credit_per_share) }}</td>
#                     <td style="color:#818cf8; font-weight:bold;">${{ "%.2f"|format(p.current_price) }}</td>
#                     <td class="{{ 'positive' if p.pnl_pct >= 0 else 'negative' }}">{{ "%+.1f"|format(p.pnl_pct) }}%</td>
#                     <td class="{{ 'positive' if p.pnl_usd >= 0 else 'negative' }}">${{ "%+.2f"|format(p.pnl_usd) }}</td>
#                     <td><button onclick="fetch('/exit',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({trade_id:'{{p.trade_id}}'})}).then(()=>location.reload())">Force Close</button></td>
#                 </tr>
#                 {% endfor %}
#             </tbody>
#         </table>
#         {% else %}
#         <p style="color:#64748b; font-style: italic;">No active risk positions tracked in open inventory. Standby for time schedule matching.</p>
#         {% endif %}
#     </div>
# </body>
# </html>
# """

# def sync_state_from_ledger():
#     if not LEDGER_PATH.exists(): return
#     try:
#         client = OptionHistoricalDataClient(api_key=config.ALPACA_API_KEY, secret_key=config.ALPACA_SECRET_KEY)
#         with open(LEDGER_PATH, 'r', encoding='utf8') as f:
#             ledger = json.load(f)
            
#         dashboard_state["account"] = ledger.get("account_summary", {})
#         raw_positions = ledger.get("active_positions", [])
        
#         for pos in raw_positions:
#             target_contract_key = pos.get("short_leg_symbol", pos.get("symbol", ""))
#             underlying_ticker = pos.get("symbol", "AMD")
            
#             pos["details"] = parse_osi_details(target_contract_key)
#             if "short_strike" not in pos:
#                 try:
#                     pos["short_strike"] = float(pos["details"]["strike"].replace("$","")) if pos["details"]["strike"] != "N/A" else 0.0
#                 except:
#                     pos["short_strike"] = 0.0
#             if "long_strike" not in pos:
#                 pos["long_strike"] = pos["short_strike"] - config.SPREAD_WIDTH_POINTS

#             from datetime import date, timedelta
#             today_dt = date.today()
#             req = OptionChainRequest(
#                 underlying_symbol=underlying_ticker,
#                 expiration_date_start=today_dt + timedelta(days=25),
#                 expiration_date_end=today_dt + timedelta(days=50)
#             )
            
#             chain_data = client.get_option_chain(req)
#             live_price = 0.0
            
#             if chain_data and target_contract_key in chain_data:
#                 snapshot = chain_data[target_contract_key]
#                 if snapshot.latest_quote:
#                     bid = float(snapshot.latest_quote.bid_price) if snapshot.latest_quote.bid_price else 0.0
#                     ask = float(snapshot.latest_quote.ask_price) if snapshot.latest_quote.ask_price else 0.0
#                     if bid > 0 and ask > 0:
#                         live_price = round((bid + ask) / 2, 2)

#             entry = float(pos.get("entry_credit_per_share", 1.0))
#             if live_price <= 0.0:
#                 live_price = entry  
                
#             pos["current_price"] = live_price
#             pos["pnl_usd"] = round((entry - live_price) * 100, 2)
#             pos["pnl_pct"] = round(((entry - live_price) / entry) * 100, 1) if entry > 0 else 0.0
            
#         dashboard_state["positions"] = raw_positions
#         dashboard_state["trade_log"] = ledger.get("closed_trades_history", [])
#         dashboard_state["last_update"] = datetime.now().strftime("%H:%M:%S")
#         dashboard_state["status_message"] = "Live verified account metrics streaming flawlessly."
#     except Exception as e:
#         dashboard_state["status_message"] = f"Live Sync Postponed: {e}"

# @app.route("/")
# def index():
#     sync_state_from_ledger()
#     return render_template_string(HTML_TEMPLATE, state=dashboard_state)

# @app.route("/reset", methods=["POST"])
# def reset_ledger_api():
#     if LEDGER_PATH.exists(): os.remove(str(LEDGER_PATH))
#     with open(LEDGER_PATH, 'w') as f:
#         json.dump({"account_summary":{"starting_capital":5000.0,"current_cash_balance":5000.0,"blocked_collateral":0.0,"total_equity":5000.0},"active_positions":[],"closed_trades_history":[]}, f)
#     return jsonify({"message": "Reset complete."})

# @app.route("/exit", methods=["POST"])
# def exit_position():
#     tid = request.json.get("trade_id")
#     with open(LEDGER_PATH, 'r') as f: ledger = json.load(f)
#     ledger["active_positions"] = [p for p in ledger.get("active_positions", []) if str(p.get("trade_id")) != str(tid)]
#     with open(LEDGER_PATH, 'w') as f: json.dump(ledger, f)
#     return jsonify({"message": "Closed manually."})


# def background_trading_orchestrator_loop():
#     """Time-Gated background thread running parallel to the web user interface."""
#     from main import TradingBotOrchestrator
#     import time
    
#     print(" --> Background Time-Gated Engine: ONLINE & ENFORCING TIME MARGINS")
#     bot = TradingBotOrchestrator()
    
#     while True:
#         try:
#             # 1. Calculate modern Python 3.14 timezone-aware US Eastern Time
#             from datetime import datetime, timedelta, UTC
#             utc_now = datetime.now(UTC)
#             utc_naive = utc_now.replace(tzinfo=None)
#             is_dst = time.localtime().tm_isdst
#             offset = timedelta(hours=-4) if is_dst else timedelta(hours=-5)
#             now_est = utc_naive + offset
            
#             # 2. Establish your target awakening time (9:30 AM open minus 30m buffer = 9:00 AM EST)
#             target_wake_time = now_est.replace(
#                 hour=config.MARKET_OPEN_HOUR, 
#                 minute=config.MARKET_OPEN_MINUTE, 
#                 second=0, 
#                 microsecond=0
#             ) - timedelta(minutes=config.AUTOMATED_WAKE_BUFFER_MINUTES)

#             # Rule A: Weekend Gate
#             if now_est.weekday() >= 5:
#                 dashboard_state["status_message"] = "Weekend Mode: Markets Closed. Background loop sleeping peacefully."
#                 time.sleep(3600)  # Check clock every hour
#                 continue

#             # Rule B: Pre-Market Standby Gate (Before 9:00 AM EST)
#             if now_est < target_wake_time:
#                 sleep_seconds = (target_wake_time - now_est).total_seconds()
#                 dashboard_state["status_message"] = f"Pre-Market Standby: Sleeping for {sleep_seconds/60:.1f} minutes until 9:00 AM EST."
#                 print(f" --> Time-Gate Alert: Current time is {now_est.strftime('%H:%M')} EST. Suspending scan loops until 9:00 AM EST.")
#                 time.sleep(sleep_seconds)
#                 continue

#             # Rule C: Post-Market Close Gate (After 4:00 PM EST)
#             market_close_time = now_est.replace(hour=16, minute=0, second=0)
#             if now_est >= market_close_time:
#                 tomorrow_wake = target_wake_time + timedelta(days=1)
#                 sleep_seconds = (tomorrow_wake - now_est).total_seconds()
#                 dashboard_state["status_message"] = f"Market Closed for the day. Sleeping for {sleep_seconds/3600:.2f} hours until tomorrow morning."
#                 print(f" --> Time-Gate Alert: Market is closed. Suspending scan loops until tomorrow morning.")
#                 time.sleep(sleep_seconds)
#                 continue

#             # 3. PASS GATES: Only executes active scans between 9:00 AM and 4:00 PM Eastern Time!
#             dashboard_state["cycle"] += 1
#             dashboard_state["status_message"] = f"Loop Cycle #{dashboard_state['cycle']}: Running active portfolio risk and market data scans..."
            
#             # Execute the core strategy pipeline files
#             bot.run_daily_scan()
            
#             dashboard_state["status_message"] = "Continuous Monitoring active. Next scan scheduled in 15 minutes."
#             time.sleep(900)  # Continuous 15-minute polling interval interval
            
#         except Exception as e:
#             dashboard_state["status_message"] = f"Background Loop Error: {e}"
#             time.sleep(60)

# # Inject initialization trigger directly into the Flask startup parameters
# threading.Thread(target=background_trading_orchestrator_loop, daemon=True).start()

# if __name__ == "__main__":
#     print("-----------------------------------------------------------------")
#     print(" ✅ Options Premium Hub active at http://127.0.0.1:5000")
#     print("-----------------------------------------------------------------")
#     app.run(host="127.0.0.1", port=5000, debug=False)
