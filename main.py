"""
程序入口 - Polymarket BTC 套利模拟程序
"""
import os
import re
import sys
import time
import signal
import logging
import threading

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

# 在导入任何模块之前先加载 .env 文件
try:
    from dotenv import load_dotenv
    _env_file = os.path.join(BASE_DIR, ".env")
    if os.path.exists(_env_file):
        load_dotenv(_env_file)
        print(f"[ENV] 已加载环境变量文件: {_env_file}")
    else:
        print(f"[ENV] .env 文件不存在: {_env_file}")
except ImportError:
    print("[ENV] python-dotenv 未安装，无法加载 .env 文件")

import config
from src.utils.logger import setup_logger, get_logger
from src.database.db_manager import DBManager
from src.api.polymarket_api import PolymarketAPI
from src.strategy.strategy_manager import StrategyManager
from src.strategy.strategy_loader import discover_strategies
from src.trading.account import AccountManager
from src.trading.simulator import TradingSimulator
from src.web.app import create_app

_running = True
_current_price = 0.0
_market_id = ""
_market_condition_id = ""
_btc_token_id = ""


def _signal_handler(sig, frame):
    global _running
    logger = get_logger()
    logger.info("收到停止信号，正在关闭...")
    _running = False


def init_database(db):
    accounts = db.get_accounts()
    if not accounts:
        aid = db.create_account("默认账户", config.ACCOUNT_DEFAULTS["initial_balance"])
        logger = get_logger()
        logger.info(f"已创建默认账户: 默认账户 (ID={aid})")


def find_btc_market(api):
    """查找合适的BTC价格追踪市场
    返回: (slug, condition_id, yes_token_id)
    """
    logger = get_logger()
    markets = api.search_btc_markets(limit=50)

    if not markets:
        logger.warning("未找到BTC相关市场，使用默认市场")
        return "will-bitcoin-drop-below-50k-in-2025", "", ""

    # 恶搞/无关市场排除词
    exclude_keywords = [
        "gta", "rihanna", "meme", "joke", "before gta",
        "before grand theft", "video game", "album",
    ]
    # 价格追踪关键词（必须包含这些之一）
    price_keywords = [
        "above", "below", "over", "under", "hit", "reach",
        "price of bitcoin", "btc price", "bitcoin price",
        "will bitcoin", "will btc",
    ]

    candidates = []  # (score, slug, condition_id, yes_token_id, question)
    fallback = None

    for m in markets:
        question = m.get("question", "").lower()
        slug = m.get("slug", "")
        condition_id = m.get("condition_id", m.get("id", ""))
        active = m.get("active", False)
        closed = m.get("closed", False)

        # 跳过已关闭的市场
        if closed:
            continue
        if not active:
            continue

        # 提取 YES token
        tokens = m.get("tokens", [])
        yes_id = ""
        for t in tokens:
            if t.get("outcome", "").upper() == "YES":
                yes_id = t.get("token_id", "")

        # 记录第一个活跃市场作为最终备选
        if not fallback:
            fallback = (slug, condition_id, yes_id, question)

        # 排除恶搞市场
        if any(kw in question for kw in exclude_keywords):
            logger.debug(f"排除非价格市场: {question[:50]}...")
            continue

        # 必须包含价格追踪关键词
        if not any(kw in question for kw in price_keywords):
            continue

        # 算分：包含价格数值的市场得分更高
        score = 0
        if "$" in question or re.search(r'\d{3,}', question):
            score += 2
        if "above" in question or "over" in question:
            score += 1
        if "below" in question or "under" in question:
            score += 1
        if yes_id:
            score += 1

        if score > 0:
            candidates.append((score, slug, condition_id, yes_id, question))

    # 按分数排序
    if candidates:
        candidates.sort(key=lambda x: x[0], reverse=True)
        score, slug, condition_id, yes_id, question = candidates[0]
        logger.info(f"找到BTC价格市场(得分{score}): {question[:60]}...")
        logger.info(f"  slug={slug}, condition_id={condition_id[:20]}..., yes_token={yes_id[:20]}...")
        return slug, condition_id, yes_id

    if fallback:
        logger.warning(f"未找到价格追踪市场，使用备选: {fallback[3][:60]}...")
        return fallback[0], fallback[1], fallback[2]

    logger.warning("未找到任何活跃BTC市场")
    return "", "", ""


def fetch_market_data(db, api, market_slug, condition_id=None, btc_token_id=None):
    global _current_price, _btc_token_id, _market_condition_id
    logger = get_logger()

    try:
        # 设置 token_id：优先用传入的，其次用全局缓存的
        if btc_token_id:
            _btc_token_id = btc_token_id
        if condition_id:
            _market_condition_id = condition_id

        # 如果还没有 token_id，尝试从市场详情获取
        if not _btc_token_id and _market_condition_id:
            market = api.get_market_by_id(_market_condition_id)
            if market:
                tokens = market.get("tokens", [])
                for t in tokens:
                    if t.get("outcome", "").upper() == "YES":
                        _btc_token_id = t.get("token_id", "")

        # 如果仍然没有，尝试通过 slug 获取
        if not _btc_token_id and market_slug:
            market = api.get_market_by_slug(market_slug)
            if market:
                # Gamma API 返回格式可能是列表或单个对象
                if isinstance(market, list):
                    market = market[0] if market else {}
                tokens = market.get("tokens", [])
                for t in tokens:
                    if t.get("outcome", "").upper() == "YES":
                        _btc_token_id = t.get("token_id", "")
                if not _market_condition_id:
                    _market_condition_id = market.get("condition_id", "")

        token_id = _btc_token_id
        if not token_id:
            logger.warning("未找到 BTC YES token ID，跳过数据获取")
            return

        logger.debug(f"使用 token_id: {token_id[:20]}...")

        # 获取K线历史数据（使用 condition_id 调用 Gamma API）
        interval = config.BTC_MARKET.get("interval", "15m")
        limit = config.BTC_MARKET.get("limit", 200)
        history_id = _market_condition_id or token_id
        kline_data = api.get_price_history(history_id, interval=interval, limit=limit)

        if kline_data:
            inserted = db.insert_market_data(market_slug, kline_data)
            if inserted > 0:
                logger.info(f"新增 {inserted} 条K线数据")
            _current_price = kline_data[-1].get("close", 0.0)
            logger.debug(f"K线收盘价: {_current_price:.6f}")
        else:
            logger.warning("未获取到K线数据")

        # 获取实时中间价（CLOB API）
        price_info = api.get_realtime_price(_btc_token_id)
        if price_info.get("mid", 0) > 0:
            _current_price = price_info["mid"]
            logger.debug(f"实时中间价: {_current_price:.6f}")

    except Exception as e:
        logger.error(f"获取市场数据失败: {e}")


def run_strategies(db, strategy_mgr, simulator, market_slug):
    logger = get_logger()
    try:
        signals = strategy_mgr.run_all_enabled(market_slug)
        if not signals:
            logger.debug("无策略信号")
            return
        results = strategy_mgr.execute_signals(signals, simulator)
        simulator.update_snapshots(_current_price)
        logger.info(f"本轮执行完成: {len(signals)} 个信号, {len(results)} 笔交易")
    except Exception as e:
        logger.error(f"策略执行失败: {e}")


def scheduler_loop(db, api, strategy_mgr, simulator, market_slug,
                    condition_id=None, btc_token_id=None):
    global _running
    logger = get_logger()
    fetch_interval = config.BTC_MARKET.get("fetch_interval", 60)
    last_fetch = 0
    logger.info(f"调度器启动 | 数据获取间隔: {fetch_interval}s")

    while _running:
        now = time.time()
        if now - last_fetch >= fetch_interval:
            fetch_market_data(db, api, market_slug, condition_id, btc_token_id)
            last_fetch = now
            run_strategies(db, strategy_mgr, simulator, market_slug)
        time.sleep(5)

    logger.info("调度器已停止")


def main():
    global _running, _market_id

    setup_logger(
        name="bot",
        log_file=config.LOGGING["file"],
        level=getattr(logging, config.LOGGING["level"]),
        max_bytes=config.LOGGING["max_bytes"],
        backup_count=config.LOGGING["backup_count"],
    )
    logger = get_logger()

    logger.info("=" * 60)
    logger.info("Polymarket Jack BTC 套利模拟程序 启动")
    logger.info("=" * 60)

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    db = DBManager(config.DB_PATH)
    init_database(db)
    logger.info(f"数据库初始化完成: {config.DB_PATH}")

    api = PolymarketAPI()
    market_slug, condition_id, yes_token_id = find_btc_market(api)
    _market_id = market_slug
    _market_condition_id = condition_id
    _btc_token_id = yes_token_id
    logger.info(f"BTC市场: slug={market_slug}, condition_id={condition_id[:20] if condition_id else 'N/A'}...")

    strategy_mgr = StrategyManager(db, api)
    strategy_mgr.sync_strategies_to_db()
    logger.info("策略管理器初始化完成")

    acc_mgr = AccountManager(db)
    acc_mgr.load_accounts()

    simulator = TradingSimulator(db, acc_mgr, config.ACCOUNT_DEFAULTS)

    fetch_market_data(db, api, market_slug, condition_id, yes_token_id)

    scheduler_thread = threading.Thread(
        target=scheduler_loop,
        args=(db, api, strategy_mgr, simulator, market_slug, condition_id, yes_token_id),
        daemon=True,
        name="Scheduler"
    )
    scheduler_thread.start()
    logger.info("调度器线程已启动")

    app = create_app(
        db_manager=db,
        strategy_manager=strategy_mgr,
        simulator=simulator,
        polymarket_api=api,
        market_id=market_slug,
        market_slug=market_slug,
    )

    logger.info(f"Web Dashboard 启动: http://{config.WEB['host']}:{config.WEB['port']}")
    app.run(
        host=config.WEB["host"],
        port=config.WEB["port"],
        debug=config.WEB["debug"],
        use_reloader=False,
    )


if __name__ == "__main__":
    main()
