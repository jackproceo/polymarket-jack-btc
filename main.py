"""
程序入口 - Polymarket BTC 套利模拟程序
"""
import os
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
    """查找合适的BTC价格追踪市场"""
    logger = get_logger()
    markets = api.search_btc_markets(limit=50)

    if not markets:
        logger.warning("未找到BTC相关市场，使用默认市场")
        return "will-bitcoin-drop-below-50k-in-2025", ""

    # 优先找包含价格追踪关键词的市场
    priority_keywords = ["above", "below", "price", "will bitcoin", "btc above", "btc below"]
    fallback = None

    for m in markets:
        question = m.get("question", "").lower()
        slug = m.get("slug", m.get("condition_id", ""))
        active = m.get("active", False)

        # 记录第一个活跃市场作为备选
        if active and not fallback:
            tokens = m.get("tokens", [])
            yes_id = ""
            for t in tokens:
                if t.get("outcome", "").upper() == "YES":
                    yes_id = t.get("token_id", "")
                    break
            fallback = (slug, yes_id, question)

        # 优先选择包含价格关键词的活跃市场
        if active and any(kw in question for kw in priority_keywords):
            tokens = m.get("tokens", [])
            yes_id = ""
            for t in tokens:
                if t.get("outcome", "").upper() == "YES":
                    yes_id = t.get("token_id", "")
                    break
            logger.info(f"找到BTC价格市场: {m.get('question', slug)[:60]}... | YES token: {yes_id[:16] if yes_id else 'N/A'}...")
            return slug, yes_id

    if fallback:
        logger.info(f"使用备选BTC市场: {fallback[2][:60]}...")
        return fallback[0], fallback[1]

    m = markets[0]
    slug = m.get("slug", m.get("condition_id", ""))
    logger.info(f"使用第一个可用市场: {m.get('question', slug)}")
    return slug, ""


def fetch_market_data(db, api, market_slug):
    global _current_price, _btc_token_id
    logger = get_logger()

    try:
        market = api.get_market_by_slug(market_slug)
        if market:
            tokens = market.get("tokens", [])
            for t in tokens:
                if t.get("outcome", "").upper() == "YES":
                    _btc_token_id = t.get("token_id", "")
                    break

        token_id = _btc_token_id or market_slug
        interval = config.BTC_MARKET.get("interval", "15m")
        limit = config.BTC_MARKET.get("limit", 200)
        kline_data = api.get_price_history(token_id, interval=interval, limit=limit)

        if kline_data:
            inserted = db.insert_market_data(market_slug, kline_data)
            if inserted > 0:
                logger.info(f"新增 {inserted} 条K线数据")
            if kline_data:
                _current_price = kline_data[-1].get("close", 0.0)
        else:
            logger.warning("未获取到K线数据")

        if _btc_token_id:
            price_info = api.get_realtime_price(_btc_token_id)
            _current_price = price_info.get("mid", _current_price)
            logger.debug(f"实时价格: {_current_price:.6f}")

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


def scheduler_loop(db, api, strategy_mgr, simulator, market_slug):
    global _running
    logger = get_logger()
    fetch_interval = config.BTC_MARKET.get("fetch_interval", 60)
    last_fetch = 0
    logger.info(f"调度器启动 | 数据获取间隔: {fetch_interval}s")

    while _running:
        now = time.time()
        if now - last_fetch >= fetch_interval:
            fetch_market_data(db, api, market_slug)
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
    market_slug, _ = find_btc_market(api)
    _market_id = market_slug
    logger.info(f"BTC市场: {market_slug}")

    strategy_mgr = StrategyManager(db, api)
    strategy_mgr.sync_strategies_to_db()
    logger.info("策略管理器初始化完成")

    acc_mgr = AccountManager(db)
    acc_mgr.load_accounts()

    simulator = TradingSimulator(db, acc_mgr, config.ACCOUNT_DEFAULTS)

    fetch_market_data(db, api, market_slug)

    scheduler_thread = threading.Thread(
        target=scheduler_loop,
        args=(db, api, strategy_mgr, simulator, market_slug),
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
