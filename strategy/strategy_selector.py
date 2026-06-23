import sys
import os
import logging
from pathlib import Path

current_file = Path(__file__).resolve()
root_dir     = current_file.parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))
os.chdir(str(root_dir))

import config

logger = logging.getLogger("OptionsTradingSystem")


class StrategySelector:

    def __init__(self):
        pass

    def find_best_trade(self, candidates_list: list) -> dict:
        """
        Ranks pre-filtered spread candidates (already passed IV rank, MA50,
        and earnings filters in main.py) by composite score and selects the best.
        Expects a FLAT dict structure — no nested 'metrics' or 'trade_structure'.
        """
        valid_trades = []

        logger.info(f"[SELECTOR] Evaluating {len(candidates_list)} pre-filtered candidates...")

        for item in candidates_list:
            symbol         = item["symbol"]
            credit         = item["net_credit"]
            spread_width   = item.get("spread_width", config.SPREAD_WIDTH_POINTS)
            short_delta    = abs(item["short_delta"])
            iv_rank        = item["iv_rank"]

            max_loss        = spread_width - credit
            if max_loss <= 0:
                logger.warning(f"[SELECTOR] {symbol} REJECTED — max_loss <= 0 (credit ${credit:.2f} >= width ${spread_width:.2f})")
                continue

            return_on_risk = (credit / max_loss) * 100
            pop             = 1.0 - short_delta   # probability of profit ≈ 1 - |delta|

            logger.debug(f"[SELECTOR] {symbol} | credit=${credit:.2f} | max_loss=${max_loss:.2f} | RoR={return_on_risk:.2f}% | PoP={pop*100:.1f}% | iv_rank={iv_rank:.1f} | floor={config.MIN_RETURN_ON_RISK_PCT}% | ceiling={config.MAX_RETURN_ON_RISK_PCT}%")

            # ── Final acceptance gate: minimum yield and probability of profit
            if return_on_risk < config.MIN_RETURN_ON_RISK_PCT:
                logger.info(f"[SELECTOR] {symbol} REJECTED — RoR {return_on_risk:.2f}% < floor {config.MIN_RETURN_ON_RISK_PCT}%")
                continue

            if pop < 0.68:
                logger.info(f"[SELECTOR] {symbol} REJECTED — PoP {pop*100:.1f}% < 68% minimum")
                continue

            composite_score = iv_rank * pop * return_on_risk

            # Preserve ALL fields from the original flat item, plus computed ones.
            # This ensures order_manager and decision logs get every field they need.
            trade_record = dict(item)  # shallow copy of everything main.py built
            trade_record.update({
                "net_credit":          round(credit, 2),
                "pop_pct":             round(pop * 100, 2),
                "return_on_risk_pct":  round(return_on_risk, 2),
                "max_loss":            round(max_loss * 100, 2),   # total $ per contract
                "composite_score":     round(composite_score, 2),
            })

            valid_trades.append(trade_record)
            logger.info(f"[SELECTOR] {symbol} QUALIFIED | composite_score={composite_score:.2f} | RoR={return_on_risk:.2f}% | PoP={pop*100:.1f}%")

        if not valid_trades:
            logger.warning("[SELECTOR] No candidates passed yield/PoP thresholds.")
            return {"status": "NO_TRADES_FOUND", "message": "No stocks passed return-on-risk or probability-of-profit hurdles."}

        best_trade = sorted(valid_trades, key=lambda x: x["composite_score"], reverse=True)[0]
        best_trade["status"] = "EXECUTE"

        logger.info(f"[SELECTOR] 🏆 Best trade selected: {best_trade['symbol']} | composite_score={best_trade['composite_score']}")
        return best_trade