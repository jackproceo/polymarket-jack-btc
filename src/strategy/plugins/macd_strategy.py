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

    def generate_signal(self, df: pd.DataFrame, btc_df: pd.DataFrame = None) -> dict:
        """
        生成MACD交易信号
        优先使用 BTC 真实价格 (btc_df) 计算 MACD，回退到 Polymarket 价格 (df)
        """
        # 确定用于技术指标的价格数据源
        need_len = self.params["slow_period"] + self.params["signal_period"]
        price_source = btc_df if btc_df is not None and len(btc_df) >= need_len else df

        if len(price_source) < need_len:
            return {"action": "HOLD", "confidence": 0.0, "reason": "数据不足", "price": 0.0}

        price_source = self.preprocess_df(price_source)
        source_close = price_source["close"]

        # 计算MACD（基于 BTC 真实价格或 Polymarket 价格）
        macd_line, signal_line, histogram = self.calc_macd(
            source_close,
            fast=self.params["fast_period"],
            slow=self.params["slow_period"],
            signal=self.params["signal_period"],
        )

        # 判断交叉
        macd_prev = macd_line.iloc[-2]
        signal_prev = signal_line.iloc[-2]
        macd_curr = macd_line.iloc[-1]
        signal_curr = signal_line.iloc[-1]

        source_label = "BTC" if btc_df is not None and len(btc_df) > 0 else "Polymarket"

        # 趋势过滤（基于 source price）
        trend_ok = True
        if self.params["trend_filter"] and len(source_close) >= self.params["trend_ma_period"]:
            ma_trend = source_close.rolling(window=self.params["trend_ma_period"]).mean()
            trend_ok = source_close.iloc[-1] > ma_trend.iloc[-1]

        # 交易执行价用 Polymarket YES token 价格
        exec_df = self.preprocess_df(df)
        current_price = exec_df["close"].iloc[-1] if len(exec_df) > 0 else 0.0

        reason_parts = []
        action = "HOLD"

        # 金叉 → 买入
        if self.params["buy_on_crossover"]:
            if macd_prev <= signal_prev and macd_curr > signal_curr:
                if trend_ok:
                    action = "BUY"
                    reason_parts.append(f"[{source_label}] MACD金叉")
                else:
                    reason_parts.append("MACD金叉但趋势过滤未通过")

        # 死叉 → 卖出
        if self.params["sell_on_crossunder"] and action == "HOLD":
            if macd_prev >= signal_prev and macd_curr < signal_curr:
                action = "SELL"
                reason_parts.append(f"[{source_label}] MACD死叉")

        hist_val = abs(histogram.iloc[-1])
        confidence = min(hist_val * 10, 1.0)
        reason = ", ".join(reason_parts) if reason_parts else "无信号"

        return {
            "action": action,
            "confidence": round(confidence, 4),
            "reason": reason,
            "price": round(current_price, 6),
            "macd": round(macd_curr, 6),
            "signal_line": round(signal_curr, 6),
            "histogram": round(histogram.iloc[-1], 6),
            "price_source": source_label,
        }
