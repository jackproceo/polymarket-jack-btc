"""BTC/USD 实时价格模块
使用 Binance 公共 API 获取 BTC 价格（与 Chainlink 数据流结算源一致）。
无需 API Key，免费公开。
"""
import requests
import time
from datetime import datetime, timezone
from src.utils.logger import get_logger

logger = get_logger()

# Binance 公共 API（无需鉴权）
BINANCE_BASE = "https://api.binance.com"


def _get(url: str, params: dict = None, timeout: int = 10) -> dict:
    try:
        resp = requests.get(url, params=params, timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.debug(f"BTC价格获取失败 {url}: {e}")
        return {}


def get_btc_price() -> float:
    """获取 BTC 当前价格（USDT ≈ USD）
    返回 float 价格，失败返回 0。
    """
    url = f"{BINANCE_BASE}/api/v3/ticker/price"
    data = _get(url, params={"symbol": "BTCUSDT"})
    if data and "price" in data:
        return float(data["price"])
    return 0.0


def get_btc_klines(interval: str = "1m", limit: int = 200) -> list:
    """获取 BTC K线数据
    
    :param interval: K线间隔 (1m, 5m, 15m, 1h, 4h, 1d)
    :param limit: 返回数量（最大 1000）
    :return: [{timestamp, open, high, low, close, volume}, ...]
    """
    url = f"{BINANCE_BASE}/api/v3/klines"
    data = _get(url, params={"symbol": "BTCUSDT", "interval": interval, "limit": limit})
    if not isinstance(data, list):
        return []

    klines = []
    for k in data:
        try:
            klines.append({
                "timestamp": int(k[0]) // 1000,  # Binance 返回毫秒，转秒
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5]),
            })
        except (IndexError, ValueError):
            continue
    return klines


class BTCPriceTracker:
    """BTC 价格追踪器 — 维护窗口起始价和当前价，判断涨跌方向"""

    def __init__(self):
        self.window_start_price = 0.0      # 当前窗口开始时的 BTC 价格
        self.current_price = 0.0            # 当前 BTC 价格
        self.window_start_time = 0          # 窗口开始时间戳
        self.last_update = 0

    def sync_with_window(self, window_start_ts: int):
        """窗口切换时调用：记录新窗口的起始价"""
        if window_start_ts != self.window_start_time:
            price = get_btc_price()
            if price > 0:
                self.window_start_time = window_start_ts
                self.window_start_price = price
                self.current_price = price
                logger.info(f"窗口起始BTC价格: ${price:.2f}")

    def update(self):
        """更新当前价格"""
        price = get_btc_price()
        if price > 0:
            self.current_price = price
            self.last_update = int(time.time())

    @property
    def direction(self) -> str:
        """当前窗口的 BTC 涨跌方向: "UP" | "DOWN" | "FLAT" """
        if self.window_start_price <= 0 or self.current_price <= 0:
            return "UNKNOWN"
        if self.current_price > self.window_start_price:
            return "UP"
        elif self.current_price < self.window_start_price:
            return "DOWN"
        return "FLAT"

    @property
    def change_pct(self) -> float:
        """距离窗口起始的变化百分比"""
        if self.window_start_price <= 0:
            return 0.0
        return (self.current_price - self.window_start_price) / self.window_start_price * 100
