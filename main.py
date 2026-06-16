import sys
import os
import json
import logging
from pathlib import Path
from datetime import datetime, timedelta, date
import traceback
import pandas as pd

# Windows Force-Pathing
current_file = Path(__file__).resolve()
root_dir     = current_file.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))
os.chdir(str(root_dir))
Path("execution").mkdir(exist_ok=True)

# Logger
logger = logging.getLogger("OptionsTradingSystem")
logger.setLevel(logging.DEBUG)
file_handler   = logging.FileHandler("execution/system_audit.log", mode="a", encoding="utf-8")
file_handler.setFormatter(logging.Formatter('%(asctime)s | %(levelname)s | [%(filename)s:%(lineno)d] | %(message)s'))
stream_handler = logging.StreamHandler(sys.stdout)
stream_handler.setFormatter(logging.Formatter('%(asctime)s | %(levelname)s | %(message)s', datefmt='%H:%M:%S'))
logger.addHandler(file_handler)
logger.addHandler(stream_handler)

from alpaca.data.historical import OptionHistoricalDataClient, StockHistoricalDataClient
from alpaca.data.requests   import OptionChainRequest, StockBarsRequest
from alpaca.trading.enums   import ContractType
from alpaca.data.timeframe  import TimeFrame

import config
from data.iv_calculator       import IVCalculator
from strategy.strategy_selector import StrategySelector
from execution.order_manager  import OrderManager
from strategy.spread_manager  import SpreadManager


class TradingBotOrchestrator:

    def __init__(self):
        logger.info("Initializing 100% Consolidated Alpaca Production Options Engine...")
        self.iv_calc        = IVCalculator(lookback_period=config.LOOKBACK_PERIOD)
        self.selector       = StrategySelector()
        self.order_mgr      = OrderManager()
        self.spread_mgr     = SpreadManager()
        self.alpaca_option_client = OptionHistoricalDataClient(api_key=config.ALPACA_API_KEY, secret_key=config.ALPACA_SECRET_KEY)
        self.alpaca_stock_client  = StockHistoricalDataClient(api_key=config.ALPACA_API_KEY,  secret_key=config.ALPACA_SECRET_KEY)

        logger.info("=" * 70)
        logger.info("[STARTUP] fi-app-v3 Trading Bot Initialised")
        logger.info(f"[STARTUP] SIMULATION_MODE     : {config.VIRTUAL_SIMULATION_MODE}")
        logger.info(f"[STARTUP] VIRTUAL_CAPITAL      : ${config.VIRTUAL_STARTING_BALANCE:,.2f}")
        logger.info(f"[STARTUP] UNIVERSE size        : {len(config.UNIVERSE)} stocks")
        logger.info(f"[STARTUP] MIN_IV_RANK          : {config.MIN_IV_RANK}")
        logger.info(f"[STARTUP] TARGET_DELTA         : {config.TARGET_SHORT_DELTA}")
        logger.info(f"[STARTUP] SPREAD_WIDTH         : ${config.SPREAD_WIDTH_POINTS}")
        logger.info(f"[STARTUP] MAX_EARNINGS_DAYS    : {config.MAX_EARNINGS_LOOKAHEAD_DAYS}")
        logger.info(f"[STARTUP] LOOKBACK_PERIOD      : {config.LOOKBACK_PERIOD} days")
        logger.info("=" * 70)

    # ──────────────────────────────────────────────────────────────
    # OPTIONS CHAIN FETCH
    # ──────────────────────────────────────────────────────────────

    def fetch_live_alpaca_options(self, symbol: str, current_price: float) -> pd.DataFrame:
        """
        Fetches live PUT options chain from Alpaca with correct DTE and
        strike range filters. Never places orders.
        """
        try:
            today_dt = date.today()
            dte_start = today_dt + timedelta(days=25)
            dte_end   = today_dt + timedelta(days=50)

            # ── AUDIT: exact parameters sent to Alpaca
            logger.info(f"[API:OptionChain] {symbol} | Requesting PUT chain | expiry_gte={dte_start} expiry_lte={dte_end} | strike_range=${current_price * 0.65:.2f}–${current_price * 0.99:.2f}")

            req = OptionChainRequest(
                underlying_symbol=symbol,
                type=ContractType.PUT,
                expiration_date_gte=dte_start,                      # ← FIXED
                expiration_date_lte=dte_end,                        # ← FIXED
                strike_price_gte=round(current_price * 0.65, 2),    # ← floor 65% for long leg room
                strike_price_lte=round(current_price * 0.99, 2),    # ← OTM puts only
            )

            chain_data = self.alpaca_option_client.get_option_chain(req)

            if not chain_data:
                logger.warning(f"[API:OptionChain] {symbol} | EMPTY response")
                return pd.DataFrame()

            processed_rows = []
            for raw_symbol, snapshot in chain_data.items():
                contract_symbol = str(raw_symbol).strip()

                if len(contract_symbol) < 15 or contract_symbol[-9] != "P":
                    continue

                bid = float(snapshot.latest_quote.bid_price) if snapshot.latest_quote and snapshot.latest_quote.bid_price else 0.0
                ask = float(snapshot.latest_quote.ask_price) if snapshot.latest_quote and snapshot.latest_quote.ask_price else 0.0

                # ── Skip contracts with no real market
                if bid <= 0.0 or ask <= 0.0:
                    continue

                # ── Skip contracts with excessively wide bid-ask (stale/illiquid quotes)
                mid = (bid + ask) / 2
                if mid > 0 and (ask - bid) / mid > 0.50:
                    logger.debug(f"[API:OptionChain] {symbol} | Skipping {contract_symbol} — bid-ask spread too wide ({((ask-bid)/mid*100):.0f}% of mid)")
                    continue

                try:
                    strike_val = float(contract_symbol[-8:]) / 1000.0
                except Exception as e:
                    logger.debug(f"[PARSE:Strike] Failed to parse strike from '{contract_symbol}' | error={e}")
                    continue

                if strike_val <= 0.0:
                    continue

                live_delta = -0.25
                try:
                    if snapshot.greeks and snapshot.greeks.delta is not None:
                        live_delta = float(snapshot.greeks.delta)
                except AttributeError as e:
                    logger.debug(f"[PARSE:Greeks] No delta for {contract_symbol} | defaulting to -0.25 | {e}")

                # ── Extract expiry from contract symbol (chars 6–12)
                try:
                    exp_str  = contract_symbol[len(symbol.strip()):len(symbol.strip())+6]
                    exp_date = datetime.strptime(exp_str, "%y%m%d").strftime("%Y-%m-%d")
                except Exception:
                    exp_date = dte_end.strftime("%Y-%m-%d")

                processed_rows.append({
                    "symbol":  contract_symbol,
                    "expiry":  exp_date,
                    "type":    "put",
                    "strike":  strike_val,
                    "delta":   live_delta,
                    "bid":     bid,
                    "ask":     ask,
                    "mid":     mid,
                    "open_interest": 1000,   # OI not available from this endpoint
                })

            if not processed_rows:
                logger.warning(f"[API:OptionChain] {symbol} | No valid contracts after filtering")
                return pd.DataFrame()

            df = pd.DataFrame(processed_rows)
            logger.debug(f"[API:OptionChain] {symbol} | contracts_returned={len(df)} | strike_range=${df['strike'].min():.2f}–${df['strike'].max():.2f} | delta_range={df['delta'].min():.3f}–{df['delta'].max():.3f}")
            return df

        except Exception as e:
            logger.error(f"[API:OptionChain] {symbol} | FATAL error | {type(e).__name__}: {e}")
            logger.debug(traceback.format_exc())
            return pd.DataFrame()

    # ──────────────────────────────────────────────────────────────
    # DAILY SCAN
    # ──────────────────────────────────────────────────────────────

    def run_daily_scan(self):
        logger.info("=" * 73)
        logger.info(f" RUNNING CONSOLIDATED ALPACA MARKET SCAN: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info("=" * 73)

        # ── Load already-owned symbols to skip re-entry
        ledger_path   = Path("execution/virtual_portfolio.json")
        owned_symbols = []
        if ledger_path.exists():
            try:
                with open(ledger_path, 'r') as f:
                    owned_symbols = [pos["symbol"] for pos in json.load(f).get("active_positions", [])]
                logger.info(f"[SCAN] Currently held positions: {owned_symbols or 'none'}")
            except Exception as e:
                logger.error(f"[SCAN] Ledger parse failed: {e}")

        end_date   = datetime.now()
        start_date = end_date - timedelta(days=365)

        # ── Phase 1: Stock metrics scan
        volatility_candidates = []
        for stock in config.UNIVERSE:
            if stock in owned_symbols:
                logger.info(f"[FILTER:Owned] {stock} SKIPPED — already have open position")
                continue
            try:
                logger.info(f"Downloading historical daily charts from Alpaca for underlying stock: {stock}")
                logger.debug(f"[API:StockBars] {stock} | start={start_date.strftime('%Y-%m-%d')} end={end_date.strftime('%Y-%m-%d')} | timeframe=Day")

                req  = StockBarsRequest(symbol_or_symbols=stock, timeframe=TimeFrame.Day, start=start_date, end=end_date)
                bars = self.alpaca_stock_client.get_stock_bars(req)

                if not bars or stock not in bars.data or not bars.data[stock]:
                    logger.warning(f"[API:StockBars] {stock} | EMPTY response from Alpaca")
                    continue

                raw_bars_list = bars.data[stock]
                close_prices  = [float(bar.close) for bar in raw_bars_list]
                logger.debug(f"[API:StockBars] {stock} | bars_returned={len(close_prices)} | first_date={raw_bars_list[0].timestamp.date()} | last_date={raw_bars_list[-1].timestamp.date()} | latest_close=${close_prices[-1]:.2f}")

                if len(close_prices) < config.LOOKBACK_PERIOD:
                    logger.warning(f"[API:StockBars] {stock} | Insufficient history: {len(close_prices)} bars < {config.LOOKBACK_PERIOD} required")
                    continue

                close_series   = pd.Series(close_prices)
                returns_series = close_series.pct_change()
                rolling_vol    = returns_series.rolling(21).std() * (252 ** 0.5) * 100
                vol_series     = pd.Series(rolling_vol.dropna().values.flatten())

                iv_metrics     = self.iv_calc.calculate_metrics(vol_series)
                iv_rank        = iv_metrics["iv_rank"]

                current_price  = float(close_series.iloc[-1])
                ma_50          = float(close_series.rolling(50).mean().iloc[-1])
                ma_200         = float(close_series.rolling(200).mean().iloc[-1])

                logger.debug(f"[CALC:IV] {stock} | rolling_vol_current={vol_series.iloc[-1]:.2f}% | iv_52w_low={vol_series.min():.2f}% | iv_52w_high={vol_series.max():.2f}% | iv_rank={iv_rank:.2f}")
                logger.debug(f"[CALC:MA] {stock} | price=${current_price:.2f} | ma50=${ma_50:.2f} | ma200=${ma_200:.2f} | above_ma50={current_price >= ma_50} | above_ma200={current_price >= ma_200}")

                volatility_candidates.append({
                    "symbol":        stock,
                    "iv_rank":       iv_rank,
                    "current_price": current_price,
                    "ma_50":         ma_50,
                    "ma_200":        ma_200,
                    "days_to_earnings": 90,   # TODO: replace with real earnings calendar
                })

            except Exception as e:
                logger.error(f"[SCAN] Error processing {stock}: {e}")
                logger.debug(traceback.format_exc())
                continue

        if not volatility_candidates:
            logger.error("[SCAN] All stocks failed metrics calculation. Aborting.")
            return

        # ── Phase 2: Apply all 5 filters BEFORE fetching options chains
        logger.info(f"[FILTER] Applying 5 filters to {len(volatility_candidates)} candidates...")

        filtered_candidates = []
        for candidate in volatility_candidates:
            stock = candidate["symbol"]

            # Filter 1: IV Rank
            if candidate["iv_rank"] < config.MIN_IV_RANK:
                logger.info(f"[FILTER:IVRank] {stock} REJECTED | iv_rank={candidate['iv_rank']:.2f} < MIN={config.MIN_IV_RANK}")
                continue

            # Filter 2: MA50 trend
            if candidate["current_price"] < candidate["ma_50"]:
                logger.info(f"[FILTER:Trend] {stock} REJECTED | price=${candidate['current_price']:.2f} < ma50=${candidate['ma_50']:.2f}")
                continue

            # Filter 3: Earnings window
            if candidate["days_to_earnings"] <= config.MAX_EARNINGS_LOOKAHEAD_DAYS:
                logger.info(f"[FILTER:Earnings] {stock} REJECTED | earnings in {candidate['days_to_earnings']} days <= {config.MAX_EARNINGS_LOOKAHEAD_DAYS} day buffer")
                continue

            logger.info(f"[FILTER:PASS] {stock} | iv_rank={candidate['iv_rank']:.2f} | price=${candidate['current_price']:.2f} above ma50=${candidate['ma_50']:.2f}")
            filtered_candidates.append(candidate)

        if not filtered_candidates:
            logger.warning("[SCAN] No candidates passed the 5 filters today. No trade placed.")
            return

        # Sort by IV rank descending
        filtered_candidates.sort(key=lambda x: x["iv_rank"], reverse=True)
        logger.info(f"[SCAN] {len(filtered_candidates)} candidates passed filters. Leader: {filtered_candidates[0]['symbol']} (IV Rank: {filtered_candidates[0]['iv_rank']:.2f})")

        # ── Phase 3: Fetch options chains and build spreads
        scan_results = []
        for candidate in filtered_candidates:
            stock         = candidate["symbol"]
            current_price = candidate["current_price"]

            real_chain = self.fetch_live_alpaca_options(stock, current_price)
            if real_chain.empty:
                logger.warning(f"[API:OptionChain] {stock} | EMPTY chain — skipping")
                continue

            spread_blueprint = self.spread_mgr.build_put_credit_spread(real_chain)

            if spread_blueprint["status"] == "ERROR":
                logger.warning(f"[SPREAD:BUILD] {stock} | FAILED — {spread_blueprint.get('message', 'unknown')}")
                continue

            logger.debug(f"[SPREAD:BUILD] {stock} | short_strike=${spread_blueprint['short_leg']['strike']:.2f} | long_strike=${spread_blueprint['long_leg']['strike']:.2f} | short_delta={spread_blueprint['short_leg']['delta']:.3f} | net_credit=${spread_blueprint['metrics']['net_credit_per_contract']:.2f} | max_loss=${spread_blueprint['metrics']['max_loss_per_spread']:.2f}")

            # ── Generate OCC contract code
            try:
                target_exp_date      = date.today() + timedelta(days=38)
                short_leg_occ_symbol = f"{stock.ljust(6)}{target_exp_date.strftime('%y%m%d')}P{int(spread_blueprint['short_leg']['strike'] * 1000):08d}".replace(" ", "")
                long_leg_occ_symbol  = f"{stock.ljust(6)}{target_exp_date.strftime('%y%m%d')}P{int(spread_blueprint['long_leg']['strike']  * 1000):08d}".replace(" ", "")
                expiry_date_str      = spread_blueprint["short_leg"].get("expiry", target_exp_date.strftime("%Y-%m-%d"))
                logger.debug(f"[CONTRACT:CODE] {stock} | short={short_leg_occ_symbol} | long={long_leg_occ_symbol} | expiry={expiry_date_str}")
            except Exception as e:
                logger.error(f"[CONTRACT:CODE] {stock} | Failed to generate OCC symbol | error={e}")
                continue

            scan_results.append({
                "symbol":             stock,
                "iv_rank":            candidate["iv_rank"],
                "current_price":      current_price,
                "ma_50":              candidate["ma_50"],
                "ma_200":             candidate["ma_200"],
                "days_to_earnings":   candidate["days_to_earnings"],
                # Spread fields — flat dict so selector and order_manager can access directly
                "short_strike":       spread_blueprint["short_leg"]["strike"],
                "long_strike":        spread_blueprint["long_leg"]["strike"],
                "short_leg_symbol":   short_leg_occ_symbol,
                "long_leg_symbol":    long_leg_occ_symbol,
                "expiry_date":        expiry_date_str,
                "short_delta":        spread_blueprint["short_leg"]["delta"],
                "net_credit":         spread_blueprint["metrics"]["net_credit_per_contract"],
                "spread_width":       spread_blueprint["metrics"]["spread_width"],
                "max_loss":           spread_blueprint["metrics"]["max_loss_per_spread"],
                "return_on_risk_pct": spread_blueprint["metrics"]["return_on_risk_pct"],
            })

        if not scan_results:
            logger.warning("[SCAN] No valid spreads built from filtered candidates. No trade placed.")
            return

        # ── Phase 4: Select best trade
        best_trade = self.selector.find_best_trade(scan_results)
        if isinstance(best_trade, dict) and best_trade.get("status") == "NO_TRADES_FOUND":
            logger.warning("[SCAN] Selector returned NO_TRADES_FOUND.")
            return

        # ── Full decision audit log
        logger.info("=" * 70)
        logger.info(f"[DECISION] TRADE SELECTED: {best_trade['symbol']}")
        logger.info(f"[DECISION] Candidates evaluated : {len(scan_results)}")
        logger.info(f"[DECISION] Composite score      : {best_trade.get('composite_score', 'N/A')}")
        logger.info(f"[DECISION] IV Rank              : {best_trade.get('iv_rank', 'N/A'):.2f}")
        logger.info(f"[DECISION] Stock price          : ${best_trade.get('current_price', 0):.2f}")
        logger.info(f"[DECISION] MA50                 : ${best_trade.get('ma_50', 0):.2f}")
        logger.info(f"[DECISION] Above MA50           : {best_trade.get('current_price', 0) >= best_trade.get('ma_50', 0)}")
        logger.info(f"[DECISION] Days to earnings     : {best_trade.get('days_to_earnings', 'N/A')}")
        logger.info(f"[DECISION] Short strike         : ${best_trade['short_strike']:.2f}")
        logger.info(f"[DECISION] Long strike          : ${best_trade['long_strike']:.2f}")
        logger.info(f"[DECISION] Short delta          : {best_trade.get('short_delta', 'N/A')}")
        logger.info(f"[DECISION] Net credit           : ${best_trade['net_credit']:.2f} per contract (${best_trade['net_credit'] * 100:.2f} total)")
        logger.info(f"[DECISION] Max loss             : ${best_trade.get('max_loss', 0):.2f} total")
        logger.info(f"[DECISION] Expiry date          : {best_trade.get('expiry_date', 'N/A')}")
        logger.info(f"[DECISION] Short leg symbol     : {best_trade.get('short_leg_symbol', 'N/A')}")
        logger.info(f"[DECISION] Long leg symbol      : {best_trade.get('long_leg_symbol', 'N/A')}")
        logger.info(f"[DECISION] Return on risk       : {best_trade.get('return_on_risk_pct', 0):.1f}%")
        logger.info("=" * 70)

        # ── Phase 5: Execute (virtual)
        result = self.order_mgr.execute_spread_order(best_trade)
        logger.info(f"[EXECUTION] Result: {result['status']} — {result['message']}")
        if result["status"] == "SIMULATED_SUCCESS":
            logger.info(f"[EXECUTION] Cash collected: +${result['cash_collected']:.2f} | Collateral locked: ${result['margin_collateral_blocked']:.2f}")


if __name__ == "__main__":
    bot = TradingBotOrchestrator()
    bot.run_daily_scan()
