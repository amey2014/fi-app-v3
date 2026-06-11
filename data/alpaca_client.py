import sys
import os
from pathlib import Path

# Windows Force-Pathing
current_file = Path(__file__).resolve()
root_dir = current_file.parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))
os.chdir(str(root_dir))

# Notice OptionHistoricalDataClient is singular
from alpaca.data.historical import StockHistoricalDataClient, OptionHistoricalDataClient
from alpaca.trading.client import TradingClient
import config

from dotenv import load_dotenv
load_dotenv()  # Load environment variables from .env file

class AlpacaInterface:
    def __init__(self):
        print(config.ALPACA_API_KEY, config.ALPACA_SECRET_KEY, config.ALPACA_PAPER_MODE)
        """Initializes direct clients for data and paper execution."""
        self.trading_client = TradingClient(
            api_key=config.ALPACA_API_KEY,
            secret_key=config.ALPACA_SECRET_KEY,
            paper=config.ALPACA_PAPER_MODE
        )
        
        self.stock_data_client = StockHistoricalDataClient(
            api_key=config.ALPACA_API_KEY,
            secret_key=config.ALPACA_SECRET_KEY
        )
        
        # Fixed singular naming convention
        self.options_data_client = OptionHistoricalDataClient(
            api_key=config.ALPACA_API_KEY,
            secret_key=config.ALPACA_SECRET_KEY
        )

    def get_account_summary(self) -> dict:
        """Retrieves operational liquidity status from the live broker environment."""
        try:
            account = self.trading_client.get_account()
            return {
                "equity": float(account.equity),
                "buying_power": float(account.options_buying_power),
                "currency": account.currency,
                "status": account.status
            }
        except Exception as e:
            print(f"Error querying broker status: {e}")
            return {}

if __name__ == "__main__":
    client = AlpacaInterface()
    print("Connecting to Alpaca Paper Environment...")
    summary = client.get_account_summary()
    if summary:
        print(f"Connection Successful! Active Equity: ${summary['equity']:,}")
    else:
        print("Connection Failed. Update your API keys inside config.py.")
