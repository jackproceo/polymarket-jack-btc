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

    @staticmethod
    def _normalize_token_id(raw_id) -> str:
        """将 token_id 标准化为 0x 前缀的十六进制字符串
        
        CLOB API 要求 token_id 必须是 0x 开头的 hex 格式。
        Gamma API 可能返回 decimal 字符串（如 '443058...'），需转换。
        """
        if not raw_id:
            return ""
        s = str(raw_id).strip()
        if s.startswith("0x"):
            return s
        # 检查是否是纯数字（decimal 格式）
        if s.isdigit():
            return hex(int(s))
        # 可能是 hex 但没有 0x 前缀
        try:
            int(s, 16)
            return "0x" + s
        except ValueError:
            return s

    # ==================== 事件/市场搜索 ====================

    def search_btc_updown_events(self, limit: int = 10) -> list:
        """搜索 btc-updown 15分钟事件（BTC涨跌预测）
        
        返回活跃的 btc-updown-15m 事件列表。
        每个事件包含 markets 和 token 信息。
        """
        logger.info("搜索 BTC updown 事件...")
        all_events = []

        # 方式1: 用 title 搜索 btc-updown 事件
        url = f"{self.gamma_url}/events"
        for term in ["btc-updown", "btc updown", "btc-up", "btc up"]:
            params = {"title": term, "limit": limit, "active": "true", "closed": "false"}
            data = _rate_limited_get(url, params=params)
            items = data if isinstance(data, list) else data.get("data", data.get("events", []))
            if items:
                all_events.extend(items)
                break  # 找到了就停

        # 方式2: 如果事件API没找到，用 slug 搜索
        if not all_events:
            params = {"slug": "btc-updown", "limit": limit, "active": "true"}
            data = _rate_limited_get(url, params=params)
            items = data if isinstance(data, list) else data.get("data", data.get("events", []))
            all_events.extend(items)

        # 方式3: 直接尝试常见的 slug 模式
        if not all_events:
            for suffix in ["15m", "1h", "4h"]:
                params = {"title": f"btc-updown-{suffix}", "limit": 3, "active": "true"}
                data = _rate_limited_get(url, params=params)
                items = data if isinstance(data, list) else data.get("data", data.get("events", []))
                if items:
                    all_events.extend(items)
                    break

        # 去重并过滤
        seen = set()
        events = []
        for e in all_events:
            eid = e.get("id", e.get("slug", ""))
            if eid and eid not in seen:
                seen.add(eid)
                if not e.get("closed") and e.get("active", True):
                    events.append(e)

        # 按创建时间排序，取最新的
        events.sort(key=lambda x: x.get("createdAt", x.get("startDate", "")), reverse=True)
        logger.info(f"找到 {len(events)} 个 BTC updown 事件")
        return events

    def get_event_by_slug(self, event_slug: str) -> dict:
        """通过 slug 获取事件详情（含 markets 和 tokens）"""
        url = f"{self.gamma_url}/events/{event_slug}"
        return _rate_limited_get(url)

    def get_market_by_id(self, condition_id: str) -> dict:
        """通过 condition_id 获取市场详情（使用 query param）"""
        url = f"{self.gamma_url}/markets"
        return _rate_limited_get(url, params={"id": condition_id})

    def get_market_by_slug(self, slug: str) -> dict:
        """通过 slug 获取市场详情（使用 query param 方式）"""
        url = f"{self.gamma_url}/markets"
        return _rate_limited_get(url, params={"slug": slug})

    def get_current_price(self, token_id: str) -> float:
        """获取当前中间价（CLOB API）
        
        token_id 会被自动标准化为 0x hex 格式。
        返回 0~1 之间的价格。
        """
        tid = self._normalize_token_id(token_id)
        if not tid:
            return 0.0

        # CLOB /price 端点
        url = f"{self.clob_url}/price"
        data = _rate_limited_get(url, params={"token_id": tid})
        if data:
            bid = float(data.get("bid", 0))
            ask = float(data.get("ask", 0))
            mid = data.get("midpoint")
            if mid is not None:
                return float(mid)
            if bid > 0 and ask > 0:
                return (bid + ask) / 2
            return bid or ask

        # 备用: /midpoint
        url2 = f"{self.clob_url}/midpoint"
        data = _rate_limited_get(url2, params={"token_id": tid})
        if data and data.get("midpoint") is not None:
            return float(data["midpoint"])

        return 0.0

    def get_price_history(self, token_id: str, interval: str = "15m",
                          limit: int = 200) -> list:
        """获取当前价格作为单个数据点（不再依赖不存在的 Gamma timeseries）
        
        由于 Gamma API 的 prices_history/timeseries 端点不存在，
        此方法返回当前实时价格作为唯一定价点。
        实际K线构建由调用方通过累积累 DB 数据完成。
        """
        price = self.get_current_price(token_id)
        if price > 0:
            now = int(time.time())
            return [{
                "timestamp": now,
                "open": price,
                "high": price,
                "low": price,
                "close": price,
                "volume": 0,
            }]
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
        tid = self._normalize_token_id(token_id)
        if not tid:
            return {"bid": 0, "ask": 0, "mid": 0, "token_id": ""}

        url = f"{self.clob_url}/price"
        data = _rate_limited_get(url, params={"token_id": tid})
        if data:
            bid = float(data.get("bid", 0))
            ask = float(data.get("ask", 0))
            mid = data.get("midpoint")
            if mid is None and bid > 0 and ask > 0:
                mid = (bid + ask) / 2
            return {
                "bid": bid,
                "ask": ask,
                "mid": float(mid or 0),
                "token_id": tid,
            }
        return {"bid": 0, "ask": 0, "mid": 0, "token_id": tid}

    def get_midpoint(self, token_id: str) -> float:
        """获取中间价"""
        return self.get_current_price(token_id)

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
