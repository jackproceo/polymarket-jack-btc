"""
MACD 趋势跟踪策略插件
信号：MACD线上穿信号线 → BUY；下穿 → SELL
"""
import pandas as pd
import numpy as np
from src.strategy.base_strategy import BaseStrategy


class MACDStrategy(BaseStrategy):
    """
    MACD 趋势跟踪策略
    - MACD线 上穿 Signal线（金叉）→ 买入信号
    - MACD线 下穿 Signal线（死叉）→ 卖出信号
    - 可配置趋势过滤：仅在价格高于MA200时买入
    """

    name = "MACD"
    display_name = "MACD趋势跟踪策略"
    description = "基于MACD指标金叉/死叉的趋势跟踪策略，适合捕捉中期趋势"
    default_params = {
        "fast_period": 12,
        "slow_period": 26,
        "signal_period": 9,
        "trend_filter": True,       # 是否启用趋势过滤
        "trend_ma_period": 50,     # 趋势判断MA周期
        "buy_on_crossover": True,   # 金叉时买入
        "sell_on_crossunder": True, # 死叉时卖出
    }

    def generate_signal(self, df: pd.DataFrame) -> dict:
        """
        生成MACD交易信号
        """
        if len(df) < self.params["slow_period"] + self.params["signal_period"]:
            return {"action": "HOLD", "confidence": 0.0, "reason": "数据不足", "price": 0.0}

        df = self.preprocess_df(df)
        close = df["close"]

        # 计算MACD
        macd_line, signal_line, histogram = self.calc_macd(
            close,
            fast=self.params["fast_period"],
            slow=self.params["slow_period"],
            signal=self.params["signal_period"],
        )

        # 计算前一期值（用于判断交叉）
        macd_prev = macd_line.iloc[-2]
        signal_prev = signal_line.iloc[-2]
        macd_curr = macd_line.iloc[-1]
        signal_curr = signal_line.iloc[-1]

        current_price = close.iloc[-1]

        # 趋势过滤
        trend_ok = True
        if self.params["trend_filter"] and len(close) >= self.params["trend_ma_period"]:
            ma_trend = close.rolling(window=self.params["trend_ma_period"]).mean()
            trend_ok = current_price > ma_trend.iloc[-1]

        reason_parts = []
        action = "HOLD"

        # 金叉：MACD上穿Signal
        if self.params["buy_on_crossover"]:
            if macd_prev <= signal_prev and macd_curr > signal_curr:
                if trend_ok:
                    action = "BUY"
                    reason_parts.append("MACD金叉")
                else:
                    reason_parts.append("MACD金叉但趋势过滤未通过")

        # 死叉：MACD下穿Signal
        if self.params["sell_on_crossunder"] and action == "HOLD":
            if macd_prev >= signal_prev and macd_curr < signal_curr:
                action = "SELL"
                reason_parts.append("MACD死叉")

        # 计算信号强度（基于柱状图绝对值）
        hist_val = abs(histogram.iloc[-1])
        confidence = min(hist_val * 10, 1.0)  # 简单归一化

        reason = ", ".join(reason_parts) if reason_parts else "无信号"

        return {
            "action": action,
            "confidence": round(confidence, 4),
            "reason": reason,
            "price": round(current_price, 6),
            "macd": round(macd_curr, 6),
            "signal_line": round(signal_curr, 6),
            "histogram": round(histogram.iloc[-1], 6),
        }
