import sys
import os
from pathlib import Path
import pandas as pd

current_file = Path(__file__).resolve()
root_dir = current_file.parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))
os.chdir(str(root_dir))

import config

class SpreadManager:
    def __init__(self):
        pass

    def build_put_credit_spread(self, chain_df: pd.DataFrame) -> dict:
        if chain_df.empty:
            return {"status": "ERROR", "message": "Option chain data is empty."}

        # 1. Isolate Put contracts and sort by strike price from lowest to highest
        puts = chain_df[chain_df['type'] == 'put'].copy()
        if puts.empty:
            return {"status": "ERROR", "message": "No put options available."}
            
        puts = puts.sort_values(by='strike').reset_index(drop=True)

        # 2. Select the Short Strike closest to your target Delta profile
        # Use abs() to reliably evaluate negative Put delta values
        puts['short_delta_diff'] = (puts['delta'].abs() - abs(config.TARGET_SHORT_DELTA)).abs()
        sorted_by_delta = puts.sort_values(by='short_delta_diff')
        
        short_leg = sorted_by_delta.iloc[0]
        short_strike = float(short_leg['strike'])

        # 3. Locate the Protective Long Strike exactly $5.00 below the short strike
        target_long_strike = short_strike - config.SPREAD_WIDTH_POINTS
        
        # Filter for all available option strikes below your short leg
        valid_longs = puts[puts['strike'] < short_strike].copy()
        if valid_longs.empty:
            return {"status": "ERROR", "message": f"Could not find any strikes lower than short strike ${short_strike}"}
            
        # Target the contract closest to your $5 distance marker
        valid_longs['long_strike_diff'] = (valid_longs['strike'] - target_long_strike).abs()
        long_leg = valid_longs.sort_values(by='long_strike_diff').iloc[0]
        long_strike = float(long_leg['strike'])

        # 4. Final Financial Accounting (Credit = Short Bid minus Long Ask)
        net_credit = short_leg['bid'] - long_leg['ask']
        actual_width = short_strike - long_strike
        max_loss = actual_width - net_credit

        # If options data arrays return inverted or lag numbers, enforce a baseline yield protection floor
        if net_credit <= 0.0:
            # Revert to a tight fallback check to find any valid spread pairing that collects credit
            for _, alt_long in valid_longs.sort_values(by='strike', ascending=False).iterrows():
                test_credit = short_leg['bid'] - alt_long['ask']
                if test_credit > 0.0:
                    long_leg = alt_long
                    long_strike = float(long_leg['strike'])
                    net_credit = test_credit
                    max_loss = (short_strike - long_strike) - net_credit
                    break

        return {
            "status": "SUCCESS",
            "strategy": "PUT_CREDIT_SPREAD",
            "short_leg": {
                "symbol": short_leg['symbol'],
                "strike": short_strike,
                "delta": float(short_leg['delta']),
                "bid": float(short_leg['bid'])
            },
            "long_leg": {
                "symbol": long_leg['symbol'],
                "strike": long_strike,
                "delta": float(long_leg['delta']),
                "ask": float(long_leg['ask'])
            },
            "metrics": {
                "net_credit_per_contract": round(float(net_credit), 2),
                "total_cash_collected": round(float(net_credit * 100), 2),
                "required_margin_collateral": round(float(actual_width * 100), 2),
                "max_loss_per_spread": round(float(max_loss * 100), 2),
                "probability_of_profit_est": round((1.0 - abs(short_leg['delta'])) * 100, 2)
            }
        }

