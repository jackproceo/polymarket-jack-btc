"""Polymarket REST API 封装
Gamma API: 市场元数据和事件发现
CLOB API: 实时订单簿和价格
参考: https://docs.polymarket.com
"""
import requests
import time
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from src.utils.logger import get_logger

logger = get_logger()

BASE_URLS = {
    "gamma": "https://gamma-api.polymarket.com",
    "clob": "https://clob.polymarket.com",
}

_MIN_INTERVAL = 1.0
_last_request_time = 0

# 加载 .env
try:
    from dotenv import load_dotenv
    _env_file = Path(__file__).resolve().parent.parent.parent / ".env"
    if _env_file.exists():
        load_dotenv(_env_file)
        logger.info(f"已加载环境变量文件: {_env_file}")
except ImportError:
    pass

_proxies = None
_proxy_url = os.getenv("PROXY_URL", "").strip()
if _proxy_url:
    _proxy_for_requests = _proxy_url
    if _proxy_url.startswith("socks5://"):
        _proxy_for_requests = _proxy_url.replace("socks5://", "socks5h://", 1)
    _proxies = {"http": _proxy_for_requests, "https": _proxy_for_requests}
    logger.info(f"代理已启用: {_proxy_url}")
else:
    logger.info("代理未配置，直连模式")


def _rate_limited_get(url: str, params: dict = None, timeout: int = 15) -> dict:
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


def _parse_clob_ids(market: dict) -> list:
    """解析 clobTokenIds —— Gamma API 返回的是 JSON 编码的字符串"""
    raw = market.get("clobTokenIds", market.get("clob_token_ids", ""))
    if not raw:
        return []
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return parsed
        except (json.JSONDecodeError, TypeError):
            pass
    return []


def _parse_iso_time(val) -> datetime:
    """解析 ISO 8601 时间字符串为 UTC datetime"""
    if not val:
        return None
    try:
        s = str(val).replace("Z", "+00:00")
        return datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None


class PolymarketAPI:
    """Polymarket API 客户端"""

    def __init__(self):
        self.gamma_url = BASE_URLS["gamma"]
        self.clob_url = BASE_URLS["clob"]

    # ==================== 市场发现 ====================

    def discover_btc_updown_markets(self, limit: int = 500) -> list:
        """通过 Gamma /markets 接口发现 BTC updown 市场
        
        正确方法: GET /markets?limit=500 然后通过 slug 前缀过滤。
        不能用 active=true —— 它会过滤掉 updown 系列市场。
        
        返回匹配的市场列表，按 start_time 排序。
        """
        logger.info("发现 BTC updown 市场 (Gamma /markets)...")
        url = f"{self.gamma_url}/markets"
        data = _rate_limited_get(url, params={"limit": limit})

        markets = data if isinstance(data, list) else data.get("data", [])
        if not markets:
            logger.warning("/markets 返回空列表")
            return []

        # 支持 15m、5m、1h 等不同间隔
        updown = []
        for m in markets:
            slug = m.get("slug", "")
            question = m.get("question", "")
            # 匹配 slug: btc-updown-15m-*, btc-updown-5m-*, btc-updown-1h-*
            if slug.startswith("btc-updown-"):
                updown.append(m)
            elif question.startswith("Bitcoin Up or Down -"):
                updown.append(m)

        # 按 start_time 排序
        updown.sort(key=lambda m: m.get("start_time", m.get("startTime", "")))
        logger.info(f"找到 {len(updown)} 个 BTC updown 市场 (总{len(markets)}个)")
        return updown

    def find_active_market(self, markets: list) -> dict:
        """在 updown 市场列表中找到当前活跃的那个
        
        活跃条件: start_time <= now < end_time
        如果找不到恰好活跃的，返回最近的即将开始的市场。
        """
        now = datetime.now(timezone.utc)

        active = None
        upcoming = []

        for m in markets:
            start = _parse_iso_time(m.get("start_time", m.get("startTime")))
            end = _parse_iso_time(m.get("end_time", m.get("endTime")))
            if not start or not end:
                continue

            if start <= now < end:
                active = m
                break  # 找到活跃的立即返回
            elif now < start:
                upcoming.append((start, m))

        if active:
            return active
        if upcoming:
            upcoming.sort(key=lambda x: x[0])
            return upcoming[0][1]
        return None if not markets else markets[0]

    def extract_token_info(self, market: dict) -> dict:
        """从市场数据中提取关键信息
        
        返回: {
            "slug", "question", "condition_id",
            "up_token_id", "down_token_id",
            "start_time", "end_time"
        }
        """
        clob_ids = _parse_clob_ids(market)
        condition_id = market.get("conditionId", market.get("condition_id", ""))

        # outcomes 格式可能是列表或 JSON 字符串
        outcomes = market.get("outcomes", [])
        if isinstance(outcomes, str):
            try:
                outcomes = json.loads(outcomes)
            except (json.JSONDecodeError, TypeError):
                outcomes = []

        up_token_id = clob_ids[0] if len(clob_ids) > 0 else ""
        down_token_id = clob_ids[1] if len(clob_ids) > 1 else ""

        return {
            "slug": market.get("slug", ""),
            "question": market.get("question", ""),
            "condition_id": condition_id,
            "up_token_id": up_token_id,
            "down_token_id": down_token_id,
            "start_time": market.get("start_time", market.get("startTime", "")),
            "end_time": market.get("end_time", market.get("endTime", "")),
        }

    # ==================== CLOB 价格 ====================

    def get_current_price(self, token_id: str) -> float:
        """获取 token 当前中间价（CLOB /price）
        
        token_id: decimal 字符串 (如 "81564136371631...")，不是 0x hex。
        返回 0~1 之间的价格，失败返回 0。
        """
        if not token_id:
            return 0.0

        # CLOB /price 查询
        url = f"{self.clob_url}/price"
        data = _rate_limited_get(url, params={"token_id": token_id})
        if data:
            price = data.get("price")
            if price is not None:
                return float(price)
            # 部分返回格式包含 bid/ask
            bid = float(data.get("bid", 0))
            ask = float(data.get("ask", 0))
            if bid > 0 and ask > 0:
                return (bid + ask) / 2
            return bid or ask

        return 0.0

    def get_realtime_price(self, token_id: str) -> dict:
        """获取实时价格详情"""
        if not token_id:
            return {"bid": 0, "ask": 0, "mid": 0, "token_id": ""}

        url = f"{self.clob_url}/price"
        data = _rate_limited_get(url, params={"token_id": token_id})
        if data:
            bid = float(data.get("bid", 0))
            ask = float(data.get("ask", 0))
            price = data.get("price")
            mid = float(price) if price else ((bid + ask) / 2 if bid and ask else 0)
            return {"bid": bid, "ask": ask, "mid": mid, "token_id": token_id}
        return {"bid": 0, "ask": 0, "mid": 0, "token_id": token_id}

    def get_midpoint(self, token_id: str) -> float:
        """获取中间价"""
        return self.get_current_price(token_id)

    # ==================== 市场详情 ====================

    def get_market_by_condition_id(self, condition_id: str) -> dict:
        """通过 condition_id 获取市场详情"""
        url = f"{self.gamma_url}/markets/{condition_id}"
        return _rate_limited_get(url)

    def get_event_by_slug(self, slug: str) -> dict:
        """通过 slug 获取事件详情"""
        url = f"{self.gamma_url}/events/{slug}"
        return _rate_limited_get(url)
