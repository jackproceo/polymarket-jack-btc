"""Polymarket REST API 封装
调用 Gamma API（市场数据）和 CLOB API（实时价格）
支持通过 .env 配置代理（国内环境需要）
"""
import requests
import time
import json
import os
import re
from pathlib import Path
from src.utils.logger import get_logger

logger = get_logger()

BASE_URLS = {
    "gamma": "https://gamma-api.polymarket.com",
    "clob": "https://clob.polymarket.com",
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

    def search_btc_markets(self, limit: int = 50) -> list:
        """搜索 BTC 相关市场（找价格追踪市场）"""
        logger.info("搜索BTC相关市场...")
        url = f"{self.gamma_url}/markets"
        all_markets = []

        # 搜索 "bitcoin" 关键词
        params = {"search": "bitcoin", "limit": limit, "active": "true", "closed": "false"}
        data = _rate_limited_get(url, params=params)
        if isinstance(data, list):
            all_markets.extend(data)
        elif isinstance(data, dict):
            all_markets.extend(data.get("data", []))

        # 搜索 "btc" 关键词
        params = {"search": "btc", "limit": limit, "active": "true", "closed": "false"}
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
        """通过 slug 获取市场详情（使用 query param 方式）"""
        url = f"{self.gamma_url}/markets"
        return _rate_limited_get(url, params={"slug": slug})

    def get_price_history(self, market_id: str, interval: str = "15m",
                          limit: int = 200) -> list:
        """获取市场价格历史（K线数据）
        
        尝试多个 Gamma API 端点获取 timeseries 数据。
        market_id 可以是 condition_id 或 slug。
        """
        if not market_id:
            logger.warning("market_id 为空，跳过价格数据获取")
            return []

        # 端点1: Gamma timeseries
        for endpoint in [
            f"{self.gamma_url}/markets/{market_id}/prices_history",
            f"{self.gamma_url}/markets/{market_id}/timeseries",
        ]:
            data = _rate_limited_get(endpoint, params={"interval": interval, "limit": limit, "fidelity": 60})
            if data:
                result = self._normalize_price_data(data)
                if result:
                    logger.debug(f"从 {endpoint} 获取到 {len(result)} 条K线")
                    return result

        # 端点2: 尝试从 market 详情获取 outcomePrices
        detail = self.get_market_by_id(market_id)
        if detail:
            tokens = detail.get("tokens", detail.get("clobTokenIds", []))
            prices_data = detail.get("outcomePrices", detail.get("prices", []))
            if prices_data:
                logger.debug(f"从市场详情获取到 {len(prices_data)} 条价格")
                return self._normalize_price_data(prices_data)

        logger.warning(f"无法获取K线数据: {market_id[:30]}...")
        return []

    def _normalize_price_data(self, raw_data) -> list:
        """标准化价格数据格式"""
        # 处理各种响应格式
        if isinstance(raw_data, dict):
            raw_data = raw_data.get("data", raw_data.get("prices", raw_data.get("history", raw_data)))
        if not isinstance(raw_data, list):
            return []

        result = []
        for item in raw_data:
            try:
                ts = item.get("timestamp", item.get("ts", item.get("t", 0)))
                result.append({
                    "timestamp": int(ts),
                    "open": float(item.get("open", item.get("o", 0))),
                    "high": float(item.get("high", item.get("h", 0))),
                    "low": float(item.get("low", item.get("l", 0))),
                    "close": float(item.get("close", item.get("c", item.get("price", 0)))),
                    "volume": float(item.get("volume", item.get("v", 0))),
                })
            except (ValueError, TypeError):
                continue
        return result

    def get_realtime_price(self, token_id: str) -> dict:
        """获取实时价格（买一/卖一/中间价）"""
        if not token_id:
            return {"bid": 0, "ask": 0, "mid": 0, "token_id": ""}

        # CLOB API price endpoint
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
        if not token_id:
            return 0.0
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
