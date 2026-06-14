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

        puts = chain_df[chain_df['type'] == 'put'].copy()
        if puts.empty:
            return {"status": "ERROR", "message": "No put options available."}

        puts = puts.sort_values(by='strike').reset_index(drop=True)

        # ── Compute mid price for every contract (more realistic than bid/ask)
        puts['mid'] = (puts['bid'] + puts['ask']) / 2

        # ── Filter out contracts with no real market (bid AND ask both zero)
        puts = puts[(puts['bid'] > 0) & (puts['ask'] > 0)]
        if puts.empty:
            return {"status": "ERROR", "message": "No contracts with valid bid/ask found."}

        # ── Select short strike closest to TARGET_SHORT_DELTA (0.25)
        puts['short_delta_diff'] = (puts['delta'].abs() - abs(config.TARGET_SHORT_DELTA)).abs()
        short_leg    = puts.sort_values(by='short_delta_diff').iloc[0]
        short_strike = float(short_leg['strike'])
        short_mid    = float(short_leg['mid'])

        # ── Sanity check: short strike must be below current price (OTM put)
        # We don't have current_price here so we rely on the chain being pre-filtered
        # but we can check delta — short leg delta should be between -0.05 and -0.45
        if not (-0.45 <= float(short_leg['delta']) <= -0.05):
            return {"status": "ERROR", "message": f"Short leg delta {short_leg['delta']:.3f} outside acceptable range (-0.45 to -0.05)"}

        # ── Find long strike exactly SPREAD_WIDTH_POINTS below short strike
        target_long_strike = short_strike - config.SPREAD_WIDTH_POINTS
        valid_longs = puts[puts['strike'] < short_strike].copy()

        if valid_longs.empty:
            return {"status": "ERROR", "message": f"Could not find any strikes lower than short strike ${short_strike}"}

        valid_longs['long_strike_diff'] = (valid_longs['strike'] - target_long_strike).abs()
        long_leg     = valid_longs.sort_values(by='long_strike_diff').iloc[0]
        long_strike  = float(long_leg['strike'])
        long_mid     = float(long_leg['mid'])

        # ── Net credit using mid prices (realistic simulation pricing)
        net_credit  = round(short_mid - long_mid, 2)
        actual_width = round(short_strike - long_strike, 2)
        max_loss     = round(actual_width - net_credit, 2)

        # ── Hard guards — reject the trade if numbers are unrealistic
        if net_credit <= 0:
            return {"status": "ERROR", "message": f"Net credit ${net_credit:.2f} is zero or negative — spread not viable"}

        if net_credit >= actual_width:
            return {"status": "ERROR", "message": f"Net credit ${net_credit:.2f} >= spread width ${actual_width:.2f} — impossible pricing, data error"}

        # Credit should not exceed 60% of spread width (market never prices it higher for OTM spreads)
        if net_credit > (actual_width * 0.60):
            return {"status": "ERROR", "message": f"Net credit ${net_credit:.2f} exceeds 60% of spread width ${actual_width:.2f} — likely bad data, rejecting"}

        if max_loss <= 0:
            return {"status": "ERROR", "message": f"Max loss ${max_loss:.2f} is zero or negative — data error"}

        return_on_risk = round((net_credit / max_loss) * 100, 2)

        # ── Reject if return on risk is unrealistically high (> 20% is suspicious for 0.25 delta)
        if return_on_risk > 20.0:
            return {"status": "ERROR", "message": f"Return on risk {return_on_risk:.1f}% is unrealistically high — likely bad bid/ask data, rejecting"}

        return {
            "status": "SUCCESS",
            "strategy": "PUT_CREDIT_SPREAD",
            "short_leg": {
                "symbol":  short_leg.get('symbol', ''),
                "strike":  short_strike,
                "delta":   round(float(short_leg['delta']), 4),
                "bid":     round(float(short_leg['bid']), 2),
                "ask":     round(float(short_leg['ask']), 2),
                "mid":     round(short_mid, 2),
                "expiry":  short_leg.get('expiry', ''),
            },
            "long_leg": {
                "symbol":  long_leg.get('symbol', ''),
                "strike":  long_strike,
                "delta":   round(float(long_leg['delta']), 4),
                "bid":     round(float(long_leg['bid']), 2),
                "ask":     round(float(long_leg['ask']), 2),
                "mid":     round(long_mid, 2),
                "expiry":  long_leg.get('expiry', ''),
            },
            "metrics": {
                "net_credit_per_contract":    round(net_credit, 2),
                "total_cash_collected":       round(net_credit * 100, 2),
                "spread_width":               actual_width,
                "max_loss_per_spread":        round(max_loss * 100, 2),
                "required_margin_collateral": round(max_loss * 100, 2),
                "return_on_risk_pct":         return_on_risk,
                "probability_of_profit_est":  round((1.0 - abs(float(short_leg['delta']))) * 100, 2),
            }
        }

