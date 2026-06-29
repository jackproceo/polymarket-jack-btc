"""
策略基类 - 所有策略插件必须继承此类
"""
from abc import ABC, abstractmethod
import pandas as pd
import numpy as np


class BaseStrategy(ABC):
    """
    策略基类（抽象类）
    新增策略只需继承此类，实现 generate_signal 方法，
    并放入 src/strategy/plugins/ 目录，程序会自动发现加载。
    """

    # 类属性（子类需覆盖）
    name: str = ""           # 策略唯一标识，如 "MACD"
    display_name: str = ""   # 显示名称，如 "MACD趋势跟踪策略"
    description: str = ""    # 策略描述
    default_params: dict = {} # 默认参数

    def __init__(self, params: dict = None):
        """
        初始化策略
        :param params: 策略参数（覆盖默认参数）
        """
        self.params = {**self.default_params, **(params or {})}
        self.enabled = False
        self.trades_count = 0
        self.last_signal = "HOLD"

    @abstractmethod
    def generate_signal(self, df: pd.DataFrame, btc_df: pd.DataFrame = None) -> dict:
        """
        核心方法：根据K线数据生成交易信号
        :param df: Polymarket K线DataFrame，包含 close/volume 等列
        :param btc_df: BTC真实价格K线 (Binance) - 用于MACD/MA等技术指标计算
        :return: {
            "action": "BUY" | "SELL" | "HOLD",
            "confidence": float,
            "reason": str,
            "price": float,
        }
        """
        pass

    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        计算技术指标（子类可覆盖以添加自定义指标列）
        默认不做任何计算，子类应在 generate_signal 前调用此方法
        """
        return df

    def preprocess_df(self, df: pd.DataFrame) -> pd.DataFrame:
        """预处理DataFrame：确保列名正确、按时间排序"""
        df = df.copy()
        if "timestamp" in df.columns:
            df = df.sort_values("timestamp").reset_index(drop=True)
        return df

    def is_enabled(self) -> bool:
        return self.enabled

    def set_enabled(self, enabled: bool):
        self.enabled = enabled

    def update_params(self, params: dict):
        self.params.update(params)

    def get_info(self) -> dict:
        """返回策略信息（用于API响应）"""
        return {
            "name": self.name,
            "display_name": self.display_name,
            "description": self.description,
            "default_params": self.default_params,
            "current_params": self.params,
            "enabled": self.enabled,
            "trades_count": self.trades_count,
            "last_signal": self.last_signal,
        }

    # ==================== 技术指标工具方法 ====================

    @staticmethod
    def calc_ema(series: pd.Series, period: int) -> pd.Series:
        """计算指数移动平均线"""
        return series.ewm(span=period, adjust=False).mean()

    @staticmethod
    def calc_sma(series: pd.Series, period: int) -> pd.Series:
        """计算简单移动平均线"""
        return series.rolling(window=period).mean()

    @staticmethod
    def calc_macd(series: pd.Series, fast: int = 12,
                   slow: int = 26, signal: int = 9) -> tuple:
        """
        计算MACD指标
        返回: (macd_line, signal_line, histogram)
        """
        ema_fast = series.ewm(span=fast, adjust=False).mean()
        ema_slow = series.ewm(span=slow, adjust=False).mean()
        macd_line = ema_fast - ema_slow
        signal_line = macd_line.ewm(span=signal, adjust=False).mean()
        histogram = macd_line - signal_line
        return macd_line, signal_line, histogram

    @staticmethod
    def calc_rsi(series: pd.Series, period: int = 14) -> pd.Series:
        """计算RSI指标"""
        delta = series.diff()
        gain = delta.where(delta > 0, 0).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        rs = gain / loss
        return 100 - (100 / (1 + rs))
