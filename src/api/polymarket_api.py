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
        """发现 BTC updown 市场
        
        通过 events 接口按 series_slug 查询。
        从每个 event 的 slug 中解析时间戳（如 btc-updown-15m-1782762300 → 1782762300）。
        """
        logger.info("发现 BTC updown 事件 (Gamma /events?series_slug)...")

        all_markets = []
        for series in ["btc-up-or-down-15m", "btc-up-or-down-5m", "btc-up-or-down-1h"]:
            url = f"{self.gamma_url}/events"
            params = {
                "series_slug": series,
                "closed": "false",
                "limit": limit,
                "order": "endDate",
                "ascending": "true",
            }
            data = _rate_limited_get(url, params=params)
            items = data if isinstance(data, list) else data.get("data", [])
            if items:
                for evt in items:
                    event_slug = evt.get("slug", "")
                    # 从 event slug 解析时间戳和间隔
                    ts, interval = self._parse_updown_slug(event_slug)
                    event_start = evt.get("eventStartTime", evt.get("start_time", ""))
                    event_end = evt.get("endDate", evt.get("end_time", ""))

                    for m in evt.get("markets", []):
                        m["_event_slug"] = event_slug
                        m["_event_title"] = evt.get("title", "")
                        # 确保有 start/end 时间：优先市场级别，其次事件级别，最后 slug 解析
                        if not m.get("start_time") and not m.get("startTime"):
                            m["start_time"] = event_start or f"{ts}"
                        if not m.get("end_time") and not m.get("endTime"):
                            m["end_time"] = event_end or f"{ts + interval}"
                    all_markets.extend(evt.get("markets", []))
                logger.debug(f"series_slug={series}: {len(items)}个事件")
                break

        # 备用: /markets 过滤
        if not all_markets:
            logger.info("events 端点无结果，尝试 /markets 过滤...")
            url = f"{self.gamma_url}/markets"
            data = _rate_limited_get(url, params={"limit": limit})
            markets = data if isinstance(data, list) else data.get("data", [])
            for m in markets:
                slug = m.get("slug", "")
                if slug.startswith("btc-updown-"):
                    ts, interval = self._parse_updown_slug(slug)
                    m["_event_slug"] = slug
                    if not m.get("start_time") and not m.get("startTime"):
                        m["start_time"] = f"{ts}"
                    if not m.get("end_time") and not m.get("endTime"):
                        m["end_time"] = f"{ts + interval}"
                    all_markets.append(m)

        # 按 start_time 排序
        all_markets.sort(key=lambda m: str(m.get("start_time", m.get("startTime", ""))))
        logger.info(f"找到 {len(all_markets)} 个 BTC updown 市场")
        if all_markets:
            first = all_markets[0]
            logger.info(f"  首个: slug={first.get('_event_slug','')} "
                        f"start={first.get('start_time',first.get('startTime',''))} "
                        f"end={first.get('end_time',first.get('endTime',''))}")
        return all_markets

    @staticmethod
    def _parse_updown_slug(slug: str) -> tuple:
        """从 btc-updown-15m-1782762300 解析 (timestamp, interval_seconds)"""
        if not slug or not slug.startswith("btc-updown-"):
            return (0, 900)
        parts = slug.split("-")
        # btc-updown-15m-1782762300 → parts[2]="15m", parts[3]="1782762300"
        if len(parts) >= 4:
            interval_str = parts[2]  # "15m", "5m", "1h"
            try:
                ts = int(parts[3])
            except ValueError:
                return (0, 900)
            # 解析间隔
            if interval_str.endswith("m"):
                interval = int(interval_str[:-1]) * 60
            elif interval_str.endswith("h"):
                interval = int(interval_str[:-1]) * 3600
            else:
                interval = 900
            return (ts, interval)
        return (0, 900)

    def find_active_market(self, markets: list) -> dict:
        """在 updown 市场列表中找到当前活跃的那个
        
        活跃条件: start_time <= now < end_time
        如果找不到恰好活跃的，返回最近的即将开始的市场。
        时间字段不可用时从 slug 解析。
        """
        now = datetime.now(timezone.utc)
        now_ts = int(now.timestamp())
        logger.debug(f"当前UTC时间: {now.isoformat()} (ts={now_ts})")

        active = None
        upcoming = []

        for m in markets:
            # 尝试从时间字段解析
            start = _parse_iso_time(m.get("start_time", m.get("startTime")))
            end = _parse_iso_time(m.get("end_time", m.get("endTime")))

            # 如果时间字段无效，从 slug 解析
            if not start or not end:
                slug = m.get("_event_slug", m.get("slug", ""))
                ts, interval = self._parse_updown_slug(slug)
                if ts > 0:
                    from datetime import datetime as dt
                    start = dt.fromtimestamp(ts, tz=timezone.utc)
                    end = dt.fromtimestamp(ts + interval, tz=timezone.utc)

            if not start or not end:
                continue

            slug = m.get("_event_slug", m.get("slug", ""))
            if start <= now < end:
                active = m
                logger.debug(f"✓ 活跃: {slug} ({start.strftime('%H:%M')}~{end.strftime('%H:%M')} UTC)")
                break
            elif now < start:
                upcoming.append((start, m))

        if active:
            return active
        if upcoming:
            upcoming.sort(key=lambda x: x[0])
            next_m = upcoming[0][1]
            slug = next_m.get("_event_slug", "")
            logger.info(f"无活跃市场，下一个将在 {upcoming[0][0].strftime('%H:%M')} 开始: {slug}")
            return next_m

        logger.warning("无活跃也无即将开始的市场")
        return None if not markets else markets[0]

    def extract_token_info(self, market: dict) -> dict:
        """从市场数据中提取关键信息
        
        返回: {
            "slug", "event_slug", "question", "condition_id",
            "up_token_id", "down_token_id",
            "start_time", "end_time"
        }
        """
        clob_ids = _parse_clob_ids(market)
        condition_id = market.get("conditionId", market.get("condition_id", ""))

        up_token_id = clob_ids[0] if len(clob_ids) > 0 else ""
        down_token_id = clob_ids[1] if len(clob_ids) > 1 else ""

        return {
            "slug": market.get("slug", ""),
            "event_slug": market.get("_event_slug", market.get("slug", "")),
            "question": market.get("question", market.get("title", market.get("_event_title", ""))),
            "condition_id": condition_id,
            "up_token_id": up_token_id,
            "down_token_id": down_token_id,
            "start_time": market.get("start_time", market.get("startTime", "")),
            "end_time": market.get("end_time", market.get("endTime", "")),
        }

    @staticmethod
    def _to_clob_token_id(raw_id) -> str:
        """将 Gamma API 返回的 decimal token_id 转为 CLOB 要求的 0x hex 格式"""
        if not raw_id:
            return ""
        s = str(raw_id).strip()
        if s.startswith("0x"):
            return s
        if s.isdigit():
            return hex(int(s))
        if all(c in "0123456789abcdefABCDEF" for c in s):
            return "0x" + s
        return s

    # ==================== CLOB 价格（公开无需认证） ====================

    def _get_order_book(self, token_id: str) -> dict:
        """获取订单簿（一次调用拿 bid/ask/tick_size）"""
        tid = self._to_clob_token_id(token_id)
        if not tid:
            return {}

        # GET /book?token_id=xxx（公开端点）
        url = f"{self.clob_url}/book"
        return _rate_limited_get(url, params={"token_id": tid})

    def get_current_price(self, token_id: str) -> float:
        """获取中间价"""
        book = self._get_order_book(token_id)
        if not book:
            return 0.0

        bids = book.get("bids", [])
        asks = book.get("asks", [])
        bid = float(bids[0]["price"]) if bids else 0
        ask = float(asks[0]["price"]) if asks else 0
        if bid > 0 and ask > 0:
            return (bid + ask) / 2
        return bid or ask

    def get_realtime_price(self, token_id: str) -> dict:
        """获取实时价格详情（bid/ask/mid + tick_size）"""
        book = self._get_order_book(token_id)
        if not book:
            return {"bid": 0, "ask": 0, "mid": 0, "token_id": ""}

        bids = book.get("bids", [])
        asks = book.get("asks", [])
        bid = float(bids[0]["price"]) if bids else 0
        ask = float(asks[0]["price"]) if asks else 0
        mid = (bid + ask) / 2 if bid and ask else (bid or ask)

        return {
            "bid": bid,
            "ask": ask,
            "mid": mid,
            "token_id": self._to_clob_token_id(token_id),
            "tick_size": book.get("tick_size", "0.01"),
            "bid_depth": float(bids[0]["size"]) if bids else 0,
            "ask_depth": float(asks[0]["size"]) if asks else 0,
        }

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
