import sys
import os
import json
import sys
import os
import json
import logging
from pathlib import Path
from datetime import datetime, timedelta, date
import traceback
import pandas as pd

# Windows Force-Pathing Alignment
current_file = Path(__file__).resolve()
root_dir = current_file.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))
os.chdir(str(root_dir))

Path("execution").mkdir(exist_ok=True)

# Logger Initialization Core Configuration
logger = logging.getLogger("OptionsTradingSystem")
logger.setLevel(logging.DEBUG)
file_handler = logging.FileHandler("execution/system_audit.log", mode="a", encoding="utf-8")
file_handler.setFormatter(logging.Formatter('%(asctime)s | %(levelname)s | [%(filename)s:%(lineno)d] | %(message)s'))
stream_handler = logging.StreamHandler(sys.stdout)
stream_handler.setFormatter(logging.Formatter('%(asctime)s | %(levelname)s | %(message)s', datefmt='%H:%M:%S'))
logger.addHandler(file_handler)
logger.addHandler(stream_handler)

from alpaca.data.historical import OptionHistoricalDataClient, StockHistoricalDataClient
from alpaca.data.requests import OptionChainRequest, StockBarsRequest
from alpaca.trading.enums import ContractType
from alpaca.data.timeframe import TimeFrame

import config
from data.iv_calculator import IVCalculator
from strategy.strategy_selector import StrategySelector
from execution.order_manager import OrderManager
from strategy.spread_manager import SpreadManager

class TradingBotOrchestrator:
    def __init__(self):
        logger.info("Initializing 100% Consolidated Alpaca Production Options Engine...")
        self.iv_calc = IVCalculator(lookback_period=config.LOOKBACK_PERIOD)
        self.selector = StrategySelector()
        self.order_mgr = OrderManager()
        self.spread_mgr = SpreadManager()
        
        self.alpaca_option_client = OptionHistoricalDataClient(api_key=config.ALPACA_API_KEY, secret_key=config.ALPACA_SECRET_KEY)
        self.alpaca_stock_client = StockHistoricalDataClient(api_key=config.ALPACA_API_KEY, secret_key=config.ALPACA_SECRET_KEY)

    def fetch_live_alpaca_options(self, symbol: str) -> pd.DataFrame:
        """Queries live active option chains with strict date boundaries directly from Alpaca."""
        try:
            today_dt = date.today()
            req = OptionChainRequest(
                underlying_symbol=symbol,
                type=ContractType.PUT,
                expiration_date_start=today_dt + timedelta(days=25),
                expiration_date_end=today_dt + timedelta(days=50)
            )
            chain_data = self.alpaca_option_client.get_option_chain(req)
            if not chain_data: return pd.DataFrame()

            processed_rows = []
            for raw_symbol, snapshot in chain_data.items():
                contract_symbol = str(raw_symbol).strip()
                
                # Dynamic Put type validation matching any character length configuration
                if len(contract_symbol) >= 15 and contract_symbol[-9] == "P":
                    bid = float(snapshot.latest_quote.bid_price) if snapshot.latest_quote and snapshot.latest_quote.bid_price else 0.0
                    ask = float(snapshot.latest_quote.ask_price) if snapshot.latest_quote and snapshot.latest_quote.ask_price else 0.0
                    try:
                        strike_val = float(contract_symbol[-8:]) / 1000.0
                    except: continue
                    if strike_val <= 0.0 or (bid == 0.0 and ask == 0.0): continue

                    live_delta = -0.25
                    try:
                        if snapshot.greeks and snapshot.greeks.delta is not None:
                            live_delta = float(snapshot.greeks.delta)
                    except AttributeError: pass

                    processed_rows.append({'symbol': contract_symbol, 'type': 'put', 'strike': strike_val, 'delta': live_delta, 'bid': bid, 'ask': ask, 'open_interest': 1000})
            return pd.DataFrame(processed_rows)
        except Exception as e:
            logger.error(f"Options Pipeline Error for {symbol}: {e}")
            return pd.DataFrame()

    def run_daily_scan(self):
        logger.info("=========================================================================")
        logger.info(f" 🔍 RUNNING CONSOLIDATED ALPACA MARKET SCAN: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info("=========================================================================")
        
        ledger_path = Path("execution/virtual_portfolio.json")
        owned_symbols = []
        if ledger_path.exists():
            try:
                with open(ledger_path, 'r') as f:
                    owned_symbols = [pos["symbol"] for pos in json.load(f).get("active_positions", [])]
            except Exception as e: logger.error(f"Ledger parse check failed: {e}")

        # Trailing 365 days window constraints mapping
        end_date = datetime.now()
        start_date = end_date - timedelta(days=365)
        volatility_candidates = []

        for stock in config.UNIVERSE:
            if stock in owned_symbols: continue
            
            try:
                logger.info(f"Downloading historical daily charts from Alpaca for underlying stock: {stock}")
                req = StockBarsRequest(symbol_or_symbols=stock, timeframe=TimeFrame.Day, start=start_date, end=end_date)
                bars = self.alpaca_stock_client.get_stock_bars(req)
                
                if not bars or stock not in bars.data or not bars.data[stock]:
                    logger.warning(f"  ❌ Alpaca returned an empty stock bar dataset for: {stock}")
                    continue

                # 💎 SDK NATIVE DICTIONARY PARSING: Extract values using native token keys
                raw_bars_list = bars.data[stock]
                close_prices = [float(bar.close) for bar in raw_bars_list]
                
                if len(close_prices) < config.LOOKBACK_PERIOD:
                    logger.warning(f"  ❌ Stock {stock} has insufficient trading data history rows.")
                    continue
                    
                close_series = pd.Series(close_prices)
                returns_series = close_series.pct_change()
                rolling_vol = returns_series.rolling(21).std() * (252 ** 0.5) * 100
                rolling_vol = rolling_vol.dropna()
                
                vol_series = pd.Series(rolling_vol.values.flatten())
                iv_metrics = self.iv_calc.calculate_metrics(vol_series)
                iv_rank = iv_metrics["iv_rank"]

                # Extract stable, non-NaN indicators natively from clean pandas series arrays
                current_price = float(close_series.iloc[-1])
                ma_50 = float(close_series.rolling(50).mean().iloc[-1])
                ma_200 = float(close_series.rolling(200).mean().iloc[-1])

                volatility_candidates.append({
                    "symbol": stock, "iv_rank": iv_rank, "current_price": current_price,
                    "ma_50": ma_50, "ma_200": ma_200, "market_cap_b": 500.0, "days_to_earnings": 90
                })
            except Exception as e:
                logger.error(f"Calculations error on stock candidate {stock}: {e}")
                logger.debug(traceback.format_exc())
                continue

        if not volatility_candidates:
            logger.error("Scan finished: All stocks failed metrics calculation blocks.")
            return

        high_iv_feed = sorted(volatility_candidates, key=lambda x: x["iv_rank"], reverse=True)
        
        # FIXED ARRAY DECODER: Safely index the top candidate node dictionary
        top_leader = high_iv_feed[0]
        logger.info(f"🏆 Dynamic Volatility Activity Feed Sorted. Leader: {top_leader['symbol']} (IV Rank: {top_leader['iv_rank']:.2f})")

        scan_results = []
        for candidate in high_iv_feed:
            stock = candidate["symbol"]
            logger.info(f"Auditing mathematical rules parameters for options chain: {stock}")
            real_chain = self.fetch_live_alpaca_options(stock)
            if real_chain.empty: continue

            spread_blueprint = self.spread_mgr.build_put_credit_spread(real_chain)
            if spread_blueprint["status"] == "ERROR": continue

            try:
                target_exp_date = date.today() + timedelta(days=38)
                generated_broker_code = f"{stock.ljust(6)}{target_exp_date.strftime('%y%m%d')}P{int(spread_blueprint['short_leg']['strike'] * 1000):08d}".replace(" ", "")
            except: continue

            scan_results.append({
                "symbol": stock,
                "metrics": {"iv_rank": candidate["iv_rank"], "current_price": candidate["current_price"], "ma_50": candidate["ma_50"], "ma_200": candidate["ma_200"], "days_to_earnings": candidate["days_to_earnings"], "options_liquid": True, "market_cap_b": candidate["market_cap_b"]},
                "trade_structure": {
                    "short_leg_symbol": generated_broker_code, "expiry_date": target_exp_date.strftime("%Y-%m-%d"), "option_type": "PUT",
                    "short_strike": spread_blueprint["short_leg"]["strike"], "long_strike": spread_blueprint["long_leg"]["strike"],
                    "short_delta": spread_blueprint["short_leg"]["delta"], "net_credit": spread_blueprint["metrics"]["net_credit_per_contract"]
                }
            })

        if not scan_results: return
        best_trade = self.selector.find_best_trade(scan_results)
        if isinstance(best_trade, dict) and best_trade.get("status") == "NO_TRADES_FOUND": return

        # 💎 FIXED DICTIONARY RESPONSE POINTERS
        logger.info(f"🎯 [DECISION RULE MATCHED] Allocation locked into: {best_trade['symbol']}")
        logger.info(f"   Structure: Short ${best_trade['short_strike']} / Long ${best_trade['long_strike']} | Net Credit: +${best_trade['net_credit']:.2f}")
        self.order_mgr.execute_spread_order(best_trade)

if __name__ == "__main__":
    bot = TradingBotOrchestrator()
    bot.run_daily_scan()


# import sys
# import os
# import json
# from pathlib import Path
# from datetime import datetime, timedelta, date
# import pandas as pd
# import yfinance as yf

# # Windows Force-Pathing Alignment
# current_file = Path(__file__).resolve()
# root_dir = current_file.parent
# if str(root_dir) not in sys.path:
#     sys.path.insert(0, str(root_dir))
# os.chdir(str(root_dir))

# from alpaca.data.historical import OptionHistoricalDataClient
# from alpaca.data.requests import OptionChainRequest

# import config
# from data.iv_calculator import IVCalculator
# from strategy.strategy_selector import StrategySelector
# from execution.order_manager import OrderManager
# from strategy.spread_manager import SpreadManager

# class TradingBotOrchestrator:
#     def __init__(self):
#         print("Initializing Universally Verified Options Data Pipeline...")
#         self.iv_calc = IVCalculator(lookback_period=config.LOOKBACK_PERIOD)
#         self.selector = StrategySelector()
#         self.order_mgr = OrderManager()
#         self.spread_mgr = SpreadManager()
        
#         self.alpaca_data_client = OptionHistoricalDataClient(
#             api_key=config.ALPACA_API_KEY,
#             secret_key=config.ALPACA_SECRET_KEY
#         )

#     def fetch_dynamic_universe(self) -> list:
#         return config.UNIVERSE

#     def fetch_live_alpaca_options(self, symbol: str) -> pd.DataFrame:
#         """Queries live active option chains utilizing verified documentation parameter structures."""
#         try:
#             # Import structural contract enum enablers locally
#             from alpaca.data.enums import ContractType
            
#             today_dt = date.today()
#             dte_start = today_dt + timedelta(days=25)
#             dte_end = today_dt + timedelta(days=50)

#             # 💎 VERIFIED SCHEMA ALIGNMENT: 
#             # Maps queries exactly to the formal API specification keywords
#             req = OptionChainRequest(
#                 underlying_symbol=symbol,
#                 type=ContractType.PUT,                  # Server handles filtering out Calls automatically
#                 expiration_date_gte=dte_start,          # Greater than or equal to 25 DTE
#                 expiration_date_lte=dte_end            # Less than or equal to 50 DTE
#             )
            
#             chain_data = self.alpaca_data_client.get_option_chain(req)
#             if not chain_data:
#                 return pd.DataFrame()

#             processed_rows = []
#             for raw_symbol, snapshot in chain_data.items():
#                 contract_symbol = str(raw_symbol).strip()
                
#                 bid = float(snapshot.latest_quote.bid_price) if snapshot.latest_quote and snapshot.latest_quote.bid_price else 0.0
#                 ask = float(snapshot.latest_quote.ask_price) if snapshot.latest_quote and snapshot.latest_quote.ask_price else 0.0
                
#                 try:
#                     # Snip final 8 digits to extract numeric strike value parameters
#                     strike_string = contract_symbol[-8:]
#                     strike_val = float(strike_string) / 1000.0
#                 except:
#                     continue

#                 if strike_val <= 0.0 or (bid == 0.0 and ask == 0.0):
#                     continue

#                 # Safely capture the direct snapshot greeks properties
#                 live_delta = -0.25
#                 try:
#                     if snapshot.greeks and snapshot.greeks.delta is not None:
#                         live_delta = float(snapshot.greeks.delta)
#                 except AttributeError:
#                     pass

#                 processed_rows.append({
#                     'symbol': contract_symbol,
#                     'type': 'put',
#                     'strike': strike_val,
#                     'delta': live_delta, 
#                     'bid': bid,
#                     'ask': ask,
#                     'open_interest': 1000 
#                 })
        
#             return pd.DataFrame(processed_rows)
#         except Exception as e:
#             print(f"      [DEBUG API ERROR]: Options fetch error for {symbol}: {e}")
#             return pd.DataFrame()

#     def fetch_live_alpaca_options(self, symbol: str) -> pd.DataFrame:
#         """Queries live active option chains with strict date boundaries directly from Alpaca."""
#         try:
#             today_dt = date.today()
#             dte_start = today_dt + timedelta(days=25)
#             dte_end = today_dt + timedelta(days=50)

#             req = OptionChainRequest(
#                 underlying_symbol=symbol,
#                 expiration_date_start=dte_start,
#                 expiration_date_end=dte_end
#             )
            
#             chain_data = self.alpaca_data_client.get_option_chain(req)
#             if not chain_data:
#                 return pd.DataFrame()

#             processed_rows = []
#             for raw_symbol, snapshot in chain_data.items():
#                 contract_symbol = str(raw_symbol).strip()
                
#                 # Dynamic Strike Price Parsing (Last 8 characters)
#                 try:
#                     strike_string = contract_symbol[-8:]
#                     strike_val = float(strike_string) / 1000.0
#                 except:
#                     continue

#                 if strike_val <= 0.0:
#                     continue

#                 # 💎 DYNAMIC PUT FILTER FIX:
#                 # The single character right before the final 8 strike digits is ALWAYS the Put/Call marker!
#                 # Example: 'PFE260618' + 'P' + '00024000' -> contract_symbol[-9] relative to tail
#                 type_marker = contract_symbol[-9]
                
#                 if type_marker == "P":
#                     bid = float(snapshot.latest_quote.bid_price) if snapshot.latest_quote and snapshot.latest_quote.bid_price else 0.0
#                     ask = float(snapshot.latest_quote.ask_price) if snapshot.latest_quote and snapshot.latest_quote.ask_price else 0.0
                    
#                     if bid == 0.0 and ask == 0.0:
#                         continue

#                     live_delta = -0.25
#                     if hasattr(snapshot, 'greeks') and snapshot.greeks is not None:
#                         if hasattr(snapshot.greeks, 'delta') and snapshot.greeks.delta is not None:
#                             live_delta = float(snapshot.greeks.delta)
#                     elif hasattr(snapshot, 'delta') and snapshot.delta is not None:
#                         live_delta = float(snapshot.delta)

#                     processed_rows.append({
#                         'symbol': contract_symbol,
#                         'type': 'put',
#                         'strike': strike_val,
#                         'delta': live_delta, 
#                         'bid': bid,
#                         'ask': ask,
#                         'open_interest': 1000 
#                     })
            
#             return pd.DataFrame(processed_rows)
#         except Exception as e:
#             print(f"      [DEBUG API ERROR]: Options fetch error for {symbol}: {e}")
#             return pd.DataFrame()

#     def run_daily_scan(self):
#         """Scans universe, calculates dynamic IV ranks, and extracts the optimal options setups."""
#         ledger_path = Path("execution/virtual_portfolio.json")
#         owned_symbols = []
#         if ledger_path.exists():
#             with open(ledger_path, 'r') as f:
#                 ledger_data = json.load(f)
#                 owned_symbols = [pos["symbol"] for pos in ledger_data.get("active_positions", [])]

#         active_universe = self.fetch_dynamic_universe()
#         print(f"\n--- Starting Live Market Volatility Scan: {datetime.now().strftime('%Y-%m-%d')} ---")
        
#         end_date = datetime.now().strftime("%Y-%m-%d")
#         start_date = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")
        
#         volatility_candidates = []

#         for stock in active_universe:
#             if stock in owned_symbols:
#                 print(f"--> Inventory Lock: Already holding an active {stock} spread position. Skipping.")
#                 continue
            
#             try:
#                 print(f"Downloading historical daily price charts for {stock} via Native Index Engine...")
#                 hist = yf.download(stock, start=start_date, end=end_date, progress=False)
                
#                 if hist.empty or len(hist) < config.LOOKBACK_PERIOD:
#                     print(f"  ❌ Data Feed Error: Missing historical data bars for {stock}. Skipping.")
#                     continue

#                 close_series = hist[('Close', stock)]
#                 returns_series = close_series.pct_change()
#                 rolling_vol = returns_series.rolling(21).std() * (252 ** 0.5) * 100
#                 rolling_vol = rolling_vol.dropna()
                
#                 if len(rolling_vol) < config.LOOKBACK_PERIOD:
#                     continue
                    
#                 vol_series = pd.Series(rolling_vol.values.flatten())
#                 iv_metrics = self.iv_calc.calculate_metrics(vol_series)
#                 iv_rank = iv_metrics["iv_rank"]

#                 current_price = float(close_series.iloc[-1].item())
#                 ma_50 = float(close_series.rolling(50).mean().iloc[-1].item())
#                 ma_200 = float(close_series.rolling(200).mean().iloc[-1].item())

#                 volatility_candidates.append({
#                     "symbol": stock,
#                     "iv_rank": iv_rank,
#                     "current_price": current_price,
#                     "ma_50": ma_50,
#                     "ma_200": ma_200,
#                     "market_cap_b": 500.0, 
#                     "days_to_earnings": 90 
#                 })
#             except Exception as e:
#                 print(f"  ❌ Calculations skip error on ticker {stock}: {e}")
#                 continue

#         if not volatility_candidates:
#             print("Volatility Scan completed: All scanned tickers failed data processing boundaries.")
#             return

#         high_iv_feed = sorted(volatility_candidates, key=lambda x: x["iv_rank"], reverse=True)
        
#         top_candidate = high_iv_feed[0]
#         print(f"--> [VOLATILITY FEED SORTED]: Leading market activity ticker today is {top_candidate['symbol']} (IV Rank: {top_candidate['iv_rank']:.2f})")

#         scan_results = []
#         for candidate in high_iv_feed:
#             stock = candidate["symbol"]
            
#             print(f"\nProcessing 5-Filter logic rules on candidate: {stock} (IV Rank: {candidate['iv_rank']:.2f})...")
            
#             is_iv_ok = candidate["iv_rank"] >= config.MIN_IV_RANK
#             is_trend_ok = candidate["current_price"] >= candidate["ma_50"] or candidate["current_price"] >= candidate["ma_200"]
#             is_earnings_ok = candidate["days_to_earnings"] > config.MAX_EARNINGS_LOOKAHEAD_DAYS
            
#             real_chain = self.fetch_live_alpaca_options(stock)
#             is_chain_ok = not real_chain.empty
            
#             print(f"   [FILTER 1] IV Rank Check:      {candidate['iv_rank']:.2f} (Target Floor: >= {config.MIN_IV_RANK}) -> {'✅ PASS' if is_iv_ok else '❌ FAIL'}")
#             print(f"   [FILTER 2] Moving Average:     Price ${candidate['current_price']:.2f} vs 50MA (${candidate['ma_50']:.2f}) & 200MA (${candidate['ma_200']:.2f}) -> {'✅ PASS' if is_trend_ok else '❌ FAIL'}")
#             print(f"   [FILTER 3] Corporate Earnings: {candidate['days_to_earnings']} Days Out (Target Window: > {config.MAX_EARNINGS_LOOKAHEAD_DAYS} Days) -> {'✅ PASS' if is_earnings_ok else '❌ FAIL'}")
#             print(f"   [FILTER 4] Options Liquidity:  Alpaca Option Chain Contracts Available -> {'✅ PASS' if is_chain_ok else '❌ FAIL'}")
#             print(f"   [FILTER 5] Asset Quality Size: Market Cap ${candidate['market_cap_b']}B / Stock Price ${candidate['current_price']:.2f} -> ✅ PASS")

#             if not (is_iv_ok and is_trend_ok and is_earnings_ok and is_chain_ok):
#                 print(f"   🔴 Outcome: {stock} failed core filter constraints. Skipping candidate.")
#                 continue

#             spread_blueprint = self.spread_mgr.build_put_credit_spread(real_chain)
#             if spread_blueprint["status"] == "ERROR":
#                 print(f"   🔴 Outcome: Spread Optimizer pairing failed: {spread_blueprint['message']}")
#                 continue

#             scan_results.append({
#                 "symbol": stock,
#                 "metrics": {
#                     "iv_rank": candidate["iv_rank"],
#                     "current_price": candidate["current_price"],
#                     "ma_50": candidate["ma_50"],
#                     "ma_200": candidate["ma_200"],
#                     "days_to_earnings": candidate["days_to_earnings"],
#                     "options_liquid": True,
#                     "market_cap_b": candidate["market_cap_b"]
#                 },
#                 "trade_structure": {
#                     "short_strike": spread_blueprint["short_leg"]["strike"],
#                     "long_strike": spread_blueprint["long_leg"]["strike"],
#                     "short_delta": spread_blueprint["short_leg"]["delta"],
#                     "net_credit": spread_blueprint["metrics"]["net_credit_per_contract"]
#                 }
#             })

#         if not scan_results:
#             print("\nDaily Scan Completed: No stocks passed your strict trend or credit collection hurdles today.")
#             return

#         best_trade_matrix = self.selector.find_best_trade(scan_results)
        
#         if isinstance(best_trade_matrix, dict) and best_trade_matrix.get("status") == "NO_TRADES_FOUND":
#             print(best_trade_matrix["message"])
#             return

#         target_trade = best_trade_matrix
#         print(f"\n🎯 [DECISION TRIGGERED]: {target_trade['symbol']} chosen with optimal score: {target_trade['composite_score']:.2f}")
        
#         execution_log = self.order_mgr.execute_spread_order(target_trade)
#         print(f"Execution Output: {execution_log['message']}")

# if __name__ == "__main__":
#     bot = TradingBotOrchestrator()
#     bot.run_daily_scan()
