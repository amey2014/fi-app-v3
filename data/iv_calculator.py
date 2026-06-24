import sys
import os
from pathlib import Path
import pandas as pd
import numpy as np
import config

# Windows Force-Pathing
current_file = Path(__file__).resolve()
root_dir = current_file.parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))
os.chdir(str(root_dir))

class IVCalculator:
    def __init__(self, lookback_period: int = config.LOOKBACK_PERIOD):
        """
        Initializes the calculation engine.
        :param lookback_period: 252 trading days representing a standard market year.
        """
        self.lookback = lookback_period

    def calculate_metrics(self, historical_iv: pd.Series) -> dict:
        """Computes current IV Rank and Percentile over the trailing lookback window."""
        if len(historical_iv) < self.lookback:
            raise ValueError(f"Insufficient historical bars. Found {len(historical_iv)}, required {self.lookback}")

        # Slice to ensure we look at exactly the trailing year
        window_data = historical_iv.tail(self.lookback)
        if len(window_data) < self.lookback:
            raise ValueError(f"Too many NaN values. Only {len(window_data)} clean bars.")
            
        current_iv = window_data.iloc[-1]

        # Metric 1: IV Rank
        min_iv = window_data.min()
        max_iv = window_data.max()
        iv_rank = ((current_iv - min_iv) / (max_iv - min_iv) * 100) if max_iv != min_iv else 0.0

        # Metric 2: IV Percentile
        days_below = np.sum(window_data < current_iv)
        iv_percentile = (days_below / self.lookback) * 100

        return {
            "current_iv": round(current_iv, 4),
            "iv_rank": round(iv_rank, 2),
            "iv_percentile": round(iv_percentile, 2)
        }

if __name__ == "__main__":
    print("Testing Volatility Math Engine with Mock Data...")
    # Simulating 300 trading days of Implied Volatility
    np.random.seed(42)
    mock_series = pd.Series(np.random.uniform(0.15, 0.45, 300))
    
    calculator = IVCalculator()
    results = calculator.calculate_metrics(mock_series)
    
    print(f"\n[Math Success]")
    print(f"Current Simulated IV: {round(results['current_iv'] * 100, 2)}%")
    print(f"Calculated IV Rank:   {results['iv_rank']}/100")
    print(f"IV Percentile:        {results['iv_percentile']}%")