"""Polymarket REST API 封装
直接调用 Gamma API、Data API、CLOB API
支持通过 .env 配置代理（国内环境需要）
"""
import requests
import time
import json
import os
from pathlib import Path
from src.utils.logger import get_logger

logger = get_logger()

BASE_URLS = {
    "gamma": "https://gamma-api.polymarket.com",
    "clob": "https://clob.polymarket.com",
    "data": "https://data-api.polymarket.com",
}

# 请求间隔（避免触发速率限制）
_MIN_INTERVAL = 1.0
_last_request_time = 0

# 加载 .env 文件（在模块顶部确保最早加载）
try:
    from dotenv import load_dotenv
    _env_file = Path(__file__).resolve().parent.parent.parent / ".env"
    if _env_file.exists():
        load_dotenv(_env_file)
        logger.info(f"已加载环境变量文件: {_env_file}")
    else:
        logger.warning(f".env 文件不存在: {_env_file}")
except ImportError:
    logger.warning("python-dotenv 未安装，无法加载 .env 文件（请运行 pip install python-dotenv pysocks）")

# 构建代理配置（在 dotenv 加载后再读取环境变量）
_proxies = None
_proxy_url = os.getenv("PROXY_URL", "").strip()
if _proxy_url:
    # 修复 SOCKS5 代理 SSL 问题：
    # socks5://  = 本地DNS解析（可能导致SSL错误）
    # socks5h:// = 远程DNS解析（推荐用于HTTPS）
    _proxy_for_requests = _proxy_url
    if _proxy_url.startswith("socks5://"):
        _proxy_for_requests = _proxy_url.replace("socks5://", "socks5h://", 1)
    elif _proxy_url.startswith("socks4://"):
        _proxy_for_requests = _proxy_url.replace("socks4://", "socks4a://", 1)

    _proxies = {
        "http": _proxy_for_requests,
        "https": _proxy_for_requests,
    }
    logger.info(f"代理已启用: {_proxy_url} (requests使用: {_proxy_for_requests})")
else:
    _http_proxy = os.getenv("HTTP_PROXY", "") or os.getenv("http_proxy", "")
    _https_proxy = os.getenv("HTTPS_PROXY", "") or os.getenv("https_proxy", "")
    if _http_proxy or _https_proxy:
        _proxies = {}
        if _http_proxy:
            _proxies["http"] = _http_proxy
        if _https_proxy:
            _proxies["https"] = _https_proxy
        logger.info(f"代理已启用: HTTP={_http_proxy} HTTPS={_https_proxy}")
    else:
        logger.info("代理未配置，直连模式（适用于欧洲服务器部署）")


def _rate_limited_get(url: str, params: dict = None, timeout: int = 15) -> dict:
    """带速率限制的 GET 请求（支持代理）"""
    global _last_request_time
    elapsed = time.time() - _last_request_time
    if elapsed < _MIN_INTERVAL:
        time.sleep(_MIN_INTERVAL - elapsed)

    try:
        resp = requests.get(url, params=params, timeout=timeout, proxies=_proxies)
        _last_request_time = time.time()
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.RequestException as e:
        logger.error(f"API请求失败 {url}: {e}")
        return {}


class PolymarketAPI:
    """Polymarket API 客户端"""

    def __init__(self):
        self.gamma_url = BASE_URLS["gamma"]
        self.clob_url = BASE_URLS["clob"]
        self.data_url = BASE_URLS["data"]

    def search_btc_markets(self, limit: int = 50) -> list:
        """搜索 BTC 相关市场（找价格追踪市场）"""
        logger.info("搜索BTC相关市场...")
        url = f"{self.gamma_url}/markets"
        # 尝试多个搜索方式
        all_markets = []
        # 方式1: 搜索 "bitcoin" 关键词
        params = {"search": "bitcoin", "limit": limit, "active": "true"}
        data = _rate_limited_get(url, params=params)
        if isinstance(data, list):
            all_markets.extend(data)
        elif isinstance(data, dict):
            all_markets.extend(data.get("data", []))

        # 方式2: 搜索 "btc" 关键词
        params = {"search": "btc", "limit": limit, "active": "true"}
        data = _rate_limited_get(url, params=params)
        if isinstance(data, list):
            all_markets.extend(data)
        elif isinstance(data, dict):
            all_markets.extend(data.get("data", []))

        # 方式3: 获取热门市场，手动过滤
        params = {"limit": limit, "active": "true", "closed": "false"}
        data = _rate_limited_get(url, params=params)
        if isinstance(data, list):
            all_markets.extend(data)
        elif isinstance(data, dict):
            all_markets.extend(data.get("data", []))

        # 去重
        seen = set()
        unique_markets = []
        for m in all_markets:
            mid = m.get("condition_id", m.get("id", ""))
            if mid and mid not in seen:
                seen.add(mid)
                unique_markets.append(m)

        logger.info(f"找到 {len(unique_markets)} 个BTC相关市场")
        return unique_markets

    def get_market_by_id(self, condition_id: str) -> dict:
        """通过 condition_id 获取市场详情"""
        url = f"{self.gamma_url}/markets/{condition_id}"
        return _rate_limited_get(url)

    def get_market_by_slug(self, slug: str) -> dict:
        """通过 slug 获取市场详情"""
        url = f"{self.gamma_url}/markets/{slug}"
        return _rate_limited_get(url)

    def get_price_history(self, market_id: str, interval: str = "15m",
                          start_ts: int = None, end_ts: int = None,
                          limit: int = 200) -> list:
        """获取市场价格历史（K线数据）"""
        url = f"{self.data_url}/prices"
        params = {"market_id": market_id, "interval": interval, "limit": limit}
        if start_ts:
            params["startTs"] = start_ts
        if end_ts:
            params["endTs"] = end_ts

        data = _rate_limited_get(url, params=params)
        if data:
            return self._normalize_price_data(data)

        logger.warning("Data API 失败，尝试通过 /trades 构建K线...")
        return self._build_kline_from_trades(market_id, interval, limit)

    def _normalize_price_data(self, raw_data) -> list:
        """标准化价格数据格式"""
        if isinstance(raw_data, dict):
            raw_data = raw_data.get("data", raw_data.get("prices", []))
        if not isinstance(raw_data, list):
            return []

        result = []
        for item in raw_data:
            result.append({
                "timestamp": int(item.get("timestamp", item.get("ts", 0))),
                "open": float(item.get("open", item.get("o", 0))),
                "high": float(item.get("high", item.get("h", 0))),
                "low": float(item.get("low", item.get("l", 0))),
                "close": float(item.get("close", item.get("c", item.get("price", 0)))),
                "volume": float(item.get("volume", item.get("v", 0))),
            })
        return result

    def _build_kline_from_trades(self, token_id: str, interval: str = "15m", limit: int = 200) -> list:
        """从交易记录构建K线数据（降级方案）"""
        url = f"{self.clob_url}/trades"
        params = {"token_id": token_id, "limit": limit * 4}
        data = _rate_limited_get(url, params=params)
        if not data:
            return []

        trades = data if isinstance(data, list) else data.get("data", [])
        if not trades:
            return []

        interval_sec = self._interval_to_seconds(interval)
        klines = {}
        for t in trades:
            ts = int(t.get("timestamp", 0))
            bucket = (ts // interval_sec) * interval_sec
            price = float(t.get("price", 0))
            if bucket not in klines:
                klines[bucket] = {"open": price, "high": price, "low": price, "close": price, "volume": 0}
            k = klines[bucket]
            k["high"] = max(k["high"], price)
            k["low"] = min(k["low"], price)
            k["close"] = price
            k["volume"] += float(t.get("size", 1))

        return [{"timestamp": ts, **v} for ts, v in sorted(klines.items())]

    def _interval_to_seconds(self, interval: str) -> int:
        """将间隔字符串转换为秒数"""
        mapping = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "6h": 21600, "1d": 86400}
        return mapping.get(interval, 900)

    def get_realtime_price(self, token_id: str) -> dict:
        """获取实时价格（买一/卖一/中间价）"""
        url = f"{self.clob_url}/price"
        params = {"token_id": token_id}
        data = _rate_limited_get(url, params=params)
        if data:
            return {
                "bid": float(data.get("bid", 0)),
                "ask": float(data.get("ask", 0)),
                "mid": float(data.get("midpoint", (float(data.get("bid", 0)) + float(data.get("ask", 0))) / 2)),
                "token_id": token_id,
            }
        return {"bid": 0, "ask": 0, "mid": 0, "token_id": token_id}

    def get_midpoint(self, token_id: str) -> float:
        """获取中间价"""
        url = f"{self.clob_url}/midpoint"
        params = {"token_id": token_id}
        data = _rate_limited_get(url, params=params)
        if data and "midpoint" in data:
            return float(data["midpoint"])
        price = self.get_realtime_price(token_id)
        return price.get("mid", 0)

    def get_token_ids(self, condition_id: str) -> dict:
        """获取市场的 YES/NO token ID"""
        market = self.get_market_by_id(condition_id)
        if not market:
            return {"yes": "", "no": ""}

        tokens = market.get("tokens", [])
        result = {"yes": "", "no": "", "market_id": condition_id}
        for t in tokens:
            outcome = t.get("outcome", "").upper()
            if outcome == "YES":
                result["yes"] = t.get("token_id", "")
            elif outcome == "NO":
                result["no"] = t.get("token_id", "")
        return result
