import sys
import os
from pathlib import Path
import pandas as pd
import numpy as np

# Windows Force-Pathing
current_file = Path(__file__).resolve()
root_dir = current_file.parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))
os.chdir(str(root_dir))

class RegimeDetector:
    def __init__(self, trend_period: int = 14):
        self.period = trend_period

    def calculate_adx(self, df: pd.DataFrame) -> float:
        """Calculates Average Directional Index (ADX) to isolate trending vs ranging behavior."""
        df = df.copy()
        
        # True Range calculation
        df['H-L'] = df['High'] - df['Low']
        df['H-PC'] = abs(df['High'] - df['Close'].shift(1))
        df['L-PC'] = abs(df['Low'] - df['Close'].shift(1))
        df['TR'] = df[['H-L', 'H-PC', 'L-PC']].max(axis=1)
        
        # Directional Movement
        df['+DM'] = np.where((df['High'] - df['High'].shift(1)) > (df['Low'].shift(1) - df['Low']), 
                             np.maximum(df['High'] - df['High'].shift(1), 0), 0)
        df['-DM'] = np.where((df['Low'].shift(1) - df['Low']) > (df['High'] - df['High'].shift(1)), 
                             np.maximum(df['Low'].shift(1) - df['Low'], 0), 0)
        
        # Smoothed values
        tr_smooth = df['TR'].rolling(self.period).sum()
        plus_di = 100 * (df['+DM'].rolling(self.period).sum() / tr_smooth)
        minus_di = 100 * (df['-DM'].rolling(self.period).sum() / tr_smooth)
        
        # Directional Index (DX)
        dx = 100 * (abs(plus_di - minus_di) / (plus_di + minus_di))
        adx = dx.rolling(self.period).mean()
        
        return float(adx.iloc[-1]) if not np.isnan(adx.iloc[-1]) else 20.0

    def classify_regime(self, spy_df: pd.DataFrame, current_vix: float) -> str:
        """Combines structural trend tracking and volatility inputs to categorize the market."""
        adx = self.calculate_adx(spy_df)
        
        if current_vix > 25.0:
            return "HIGH_VOL"
        elif adx > 25.0:
            return "TRENDING"
        else:
            return "RANGING"

if __name__ == "__main__":
    print("Testing Macro Regime Detector Engine...")
    dates = pd.date_range(end=pd.Timestamp.now(), periods=30)
    mock_data = pd.DataFrame({
        'High': np.linspace(100, 120, 30),
        'Low': np.linspace(98, 118, 30),
        'Close': np.linspace(99, 119, 30)
    }, index=dates)
    
    detector = RegimeDetector()
    regime = detector.classify_regime(mock_data, current_vix=14.5)
    print(f"\n[Regime Success] Calculated Market State: {regime}")
