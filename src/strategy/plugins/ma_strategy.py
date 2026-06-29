"""
移动均线（MA）趋势跟踪策略插件
信号：短均线上穿长均线 → BUY；下穿 → SELL
"""
import pandas as pd
import numpy as np
from src.strategy.base_strategy import BaseStrategy


class MAStrategy(BaseStrategy):
    """
    移动均线趋势跟踪策略
    - 短周期MA上穿长周期MA（金叉）→ 买入信号
    - 短周期MA下穿长周期MA（死叉）→ 卖出信号
    - 支持 SMA 和 EMA 两种均线类型
    """

    name = "MA"
    display_name = "移动均线趋势跟踪策略"
    description = "基于双均线（短周期/长周期）金叉死叉的趋势跟踪策略"
    default_params = {
        "short_period": 20,
        "long_period": 60,
        "ma_type": "EMA",           # "SMA" 或 "EMA"
        "signal_lookback": 1,        # 检查最近N根K线的交叉
        "min_distance": 0.0,         # 均线最小距离（百分比），避免频繁交易
    }

    def generate_signal(self, df: pd.DataFrame, btc_df: pd.DataFrame = None) -> dict:
        """
        生成MA交易信号
        优先使用 BTC 真实价格 (btc_df) 计算均线，回退到 Polymarket 价格 (df)
        """
        # 确定用于技术指标的价格数据源
        price_source = btc_df if btc_df is not None and len(btc_df) >= self.params["long_period"] + 2 else df

        if len(price_source) < self.params["long_period"] + 2:
            return {"action": "HOLD", "confidence": 0.0, "reason": "数据不足", "price": 0.0}

        price_source = self.preprocess_df(price_source)
        source_close = price_source["close"]

        # 选择均线类型（基于 BTC 真实价格或 Polymarket 价格）
        if self.params["ma_type"].upper() == "EMA":
            ma_short = self.calc_ema(source_close, self.params["short_period"])
            ma_long = self.calc_ema(source_close, self.params["long_period"])
        else:
            ma_short = self.calc_sma(source_close, self.params["short_period"])
            ma_long = self.calc_sma(source_close, self.params["long_period"])

        # 获取当前和前一期值
        lookback = max(1, self.params["signal_lookback"])
        short_prev = ma_short.iloc[-(lookback + 1)]
        long_prev = ma_long.iloc[-(lookback + 1)]
        short_curr = ma_short.iloc[-1]
        long_curr = ma_long.iloc[-1]

        # 交易执行价仍用 Polymarket YES token 价格
        exec_df = self.preprocess_df(df)
        current_price = exec_df["close"].iloc[-1] if len(exec_df) > 0 else 0.0

        # 计算均线距离
        distance_pct = abs(short_curr - long_curr) / long_curr * 100 if long_curr > 0 else 0

        source_label = "BTC" if btc_df is not None and len(btc_df) > 0 else "Polymarket"
        reason_parts = []
        action = "HOLD"

        # 金叉 → 买入
        if short_prev <= long_prev and short_curr > long_curr:
            if distance_pct >= self.params["min_distance"]:
                action = "BUY"
                reason_parts.append(f"[{source_label}] {self.params['ma_type']}金叉")
            else:
                reason_parts.append(f"金叉但距离过小({distance_pct:.2f}%)")

        # 死叉 → 卖出
        elif short_prev >= long_prev and short_curr < long_curr:
            action = "SELL"
            reason_parts.append(f"[{source_label}] {self.params['ma_type']}死叉")

        # 均线位置
        elif short_curr > long_curr:
            reason_parts.append(f"[{source_label}] 多头排列")
        elif short_curr < long_curr:
            reason_parts.append(f"[{source_label}] 空头排列")

        confidence = min(distance_pct / 5.0, 1.0)
        reason = ", ".join(reason_parts) if reason_parts else "无信号"

        return {
            "action": action,
            "confidence": round(confidence, 4),
            "reason": reason,
            "price": round(current_price, 6),
            "ma_short": round(short_curr, 6),
            "ma_long": round(long_curr, 6),
            "distance_pct": round(distance_pct, 4),
            "price_source": source_label,
        }
