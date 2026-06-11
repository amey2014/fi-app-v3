import sys
import os
import time
from pathlib import Path
from datetime import datetime, timedelta, UTC

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
            print(f"--> Today is a weekend day. System sleeping for 6 hours before checking calendar...")
            time.sleep(21600)
            continue

        if now_est >= target_wake_time:
            market_close_time = now_est.replace(hour=16, minute=0, second=0)
            if now_est < market_close_time:
                print(f"--> System booted during active pre-market/trading hours window ({now_est.strftime('%H:%M')} EST). Proceeding.")
                break
            else:
                tomorrow_wake = target_wake_time + timedelta(days=1)
                sleep_seconds = (tomorrow_wake - now_est).total_seconds()
                print(f"--> Market is closed for the day. Sleeping for {sleep_seconds/3600:.2f} hours until tomorrow morning.")
                time.sleep(sleep_seconds)
                continue

        sleep_seconds = (target_wake_time - now_est).total_seconds()
        print("\n" + "="*60)
        print(f"   PRE_MARKET CLOCK ACTIVATED (Current Eastern Time: {now_est.strftime('%I:%M %p')} EST)")
        print(f"   Target System Awakening:  {target_wake_time.strftime('%I:%M %p')} EST ({config.AUTOMATED_WAKE_BUFFER_MINUTES}m before open)")
        print(f"   Entering Standby Mode:    Suspending thread execution for {sleep_seconds/60:.1f} minutes.")
        print("="*60 + "\n")
        
        time.sleep(sleep_seconds)
        print("--> Standby completed! Awakening application layers for core execution loop.")
        break

def main():
    manage_pre_market_sleep()

    print("=" * 60)
    print(f"   EXECUTING PREMIUM-HARVESTING SYSTEM WORKFLOW: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 60)

    orchestrator = TradingBotOrchestrator()
    portfolio_mgr = PortfolioUpdater()

    print("\n[WORKFLOW STEP 1] Reviewing active positions for profit takes...")
    portfolio_mgr.update_and_clean_portfolio()

    print("\n[WORKFLOW STEP 2] Scanning live market for new 5-Filter setups...")
    orchestrator.run_daily_scan()

    print("\n" + "=" * 60)
    print("   DAILY TRACKING CYCLE COMPLETED SUCCESSFULLY")
    print("=" * 60)

if __name__ == "__main__":
    main()
