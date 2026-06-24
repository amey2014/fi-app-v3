import os
from dotenv import load_dotenv
load_dotenv()  # Load environment variables from .env file

# Alpaca API Credentials (Update with your actual Paper API keys)
ALPACA_API_KEY = os.getenv("ALPACA_API_KEY")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY")
ALPACA_PAPER_MODE = True  

# VIRTUAL SIMULATION SWITCH
VIRTUAL_SIMULATION_MODE = True  
VIRTUAL_STARTING_BALANCE = 5000.0

# Watchlist Universe
UNIVERSE = ["AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "TSLA", "AMD", "BAC", "PFE"]
LOOKBACK_PERIOD = 252                 # Synchronized historical trading day count floor

# Colleague's Strategy Filters
MIN_IV_RANK = 50.0                 # Keep at 0.0 to guarantee a live entry test; change back to 50.0 later
MAX_EARNINGS_LOOKAHEAD_DAYS = 45
LIQUIDITY_MIN_OI = 500
MAX_BID_ASK_SPREAD_PCT = 0.10     

# Position Selection Parameters
TARGET_SHORT_DELTA = 0.25         
SPREAD_WIDTH_POINTS = 5.0         
MIN_RETURN_ON_RISK_PCT = 5.0      
MAX_RETURN_ON_RISK_PCT = 40

# Continuous Monitoring Time Schedule
MARKET_OPEN_HOUR = 9
MARKET_OPEN_MINUTE = 30
AUTOMATED_WAKE_BUFFER_MINUTES = 30  
BACKGROUND_POLLING_INTERVAL_SEC = 900

MAX_OPEN_SPREADS = 2
PROFIT_TARGET_PCT = 0.50
DTE_EXIT_THRESHOLD = 21
