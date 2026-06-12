import sys
import os
import time
from pathlib import Path
from datetime import datetime, timedelta, UTC
import logging
logger = logging.getLogger("OptionsTradingSystem")  # same logger as main.py

# Windows Force-Pathing
current_file = Path(__file__).resolve()
root_dir = current_file.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))
os.chdir(str(root_dir))

import config
from main import TradingBotOrchestrator
from monitoring.portfolio_updater import PortfolioUpdater

def get_eastern_time() -> datetime:
    """Calculates current time shifted to US Eastern Time using Python 3.14 standards."""
    utc_now = datetime.now(UTC)
    utc_naive = utc_now.replace(tzinfo=None)
    is_dst = time.localtime().tm_isdst
    offset = timedelta(hours=-4) if is_dst else timedelta(hours=-5)
    return utc_naive + offset

def manage_pre_market_sleep():
    """Calculates gap time to the market open and handles the countdown suspension loop."""
    while True:
        now_est = get_eastern_time()
        
        # Fixed line: Uses AUTOMATED_WAKE_BUFFER_MINUTES to match config.py exactly
        target_wake_time = now_est.replace(
            hour=config.MARKET_OPEN_HOUR, 
            minute=config.MARKET_OPEN_MINUTE, 
            second=0, 
            microsecond=0
        ) - timedelta(minutes=config.AUTOMATED_WAKE_BUFFER_MINUTES)

        if now_est.weekday() >= 5:
            logger.info(f"[SCHEDULER] Weekend detected ({now_est.strftime('%A')}). Sleeping 6 hours.")
            time.sleep(21600)
            continue

        if now_est >= target_wake_time:
            market_close_time = now_est.replace(hour=16, minute=0, second=0)
            if now_est < market_close_time:
                logger.info(f"[SCHEDULER] System active during market hours ({now_est.strftime('%H:%M')} EST). Proceeding immediately.")
                break
            else:
                tomorrow_wake = target_wake_time + timedelta(days=1)
                sleep_seconds = (tomorrow_wake - now_est).total_seconds()
                logger.info(f"[SCHEDULER] Market closed for today. Sleeping {sleep_seconds/3600:.2f}h until tomorrow wake at {tomorrow_wake.strftime('%H:%M')} EST.")
                time.sleep(sleep_seconds)
                continue

        sleep_seconds = (target_wake_time - now_est).total_seconds()
        print("\n" + "="*60)
        print(f"   PRE_MARKET CLOCK ACTIVATED (Current Eastern Time: {now_est.strftime('%I:%M %p')} EST)")
        print(f"   Target System Awakening:  {target_wake_time.strftime('%I:%M %p')} EST ({config.AUTOMATED_WAKE_BUFFER_MINUTES}m before open)")
        print(f"   Entering Standby Mode:    Suspending thread execution for {sleep_seconds/60:.1f} minutes.")
        print("="*60 + "\n")
        
        time.sleep(sleep_seconds)
        logger.info(f"Standby completed! Awakening application layers for core execution loop.")
        break

def main():
    manage_pre_market_sleep()

    logger.info("=" * 60)
    logger.info(f"[RUN] DAILY WORKFLOW STARTED: {datetime.now().strftime('%Y-%m-%d %H:%M')} EST")
    logger.info("=" * 60)

    orchestrator = TradingBotOrchestrator()
    portfolio_mgr = PortfolioUpdater()

    logger.info("[WORKFLOW:1] Reviewing active positions for exit rules (50% profit / 21 DTE)...")
    portfolio_mgr.update_and_clean_portfolio()

    logger.info("[WORKFLOW:2] Scanning live market for new 5-filter spread setups...")
    orchestrator.run_daily_scan()

    print("\n" + "=" * 60)
    logger.info("[RUN] DAILY WORKFLOW COMPLETED")
    print("=" * 60)

if __name__ == "__main__":
    main()
