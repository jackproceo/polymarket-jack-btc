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

# 加载 .env
try:
    from dotenv import load_dotenv
    _env_file = os.path.join(BASE_DIR, ".env")
    if os.path.exists(_env_file):
        load_dotenv(_env_file)
        print(f"[ENV] 已加载环境变量文件: {_env_file}")
except ImportError:
    pass

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
_market_slug = ""
_up_token_id = ""


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
    """查找当前活跃的 BTC updown 市场
    返回: dict — market_info {slug, question, condition_id, up_token_id, down_token_id, start_time, end_time}
    """
    logger = get_logger()

    # 通过 Gamma /markets 发现所有 updown 市场
    updown_markets = api.discover_btc_updown_markets(limit=500)
    if not updown_markets:
        logger.error("未找到任何 BTC updown 市场")
        return None

    # 找到当前活跃的
    active = api.find_active_market(updown_markets)
    if not active:
        logger.error("未找到当前活跃的 BTC updown 市场")
        return None

    info = api.extract_token_info(active)
    logger.info(f"BTC updown 市场: {info['question']}")
    logger.info(f"  slug={info['slug']}")
    logger.info(f"  start={info['start_time']}  end={info['end_time']}")
    logger.info(f"  up_token_id={info['up_token_id'][:30]}...")
    return info


def fetch_market_data(db, api, market_info):
    global _current_price
    logger = get_logger()

    if not market_info:
        return

    try:
        slug = market_info["slug"]
        up_id = market_info["up_token_id"]

        # 从 CLOB /price 获取当前价格
        price = api.get_current_price(up_id)
        if price <= 0:
            logger.warning(f"获取价格失败 (token={up_id[:30]}...)")
            return

        _current_price = price

        # 存入数据库
        now = int(time.time())
        kline = [{
            "timestamp": now,
            "open": price,
            "high": price,
            "low": price,
            "close": price,
            "volume": 0,
        }]
        inserted = db.insert_market_data(slug, kline)
        if inserted > 0:
            logger.info(f"价格已记录: {price:.4f} (Up token)")
        else:
            logger.debug(f"价格: {price:.4f}")

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


def scheduler_loop(db, api, strategy_mgr, simulator, market_info):
    global _running, _market_slug, _up_token_id
    logger = get_logger()
    fetch_interval = config.BTC_MARKET.get("fetch_interval", 60)
    rediscover_interval = 300  # 每5分钟重新发现市场（应对窗口切换）
    last_fetch = 0
    last_discover = 0
    logger.info(f"调度器启动 | 数据获取间隔: {fetch_interval}s | 市场重发现间隔: {rediscover_interval}s")

    while _running:
        now = time.time()

        # 定期重新发现市场
        if now - last_discover >= rediscover_interval:
            new_info = find_btc_market(api)
            if new_info:
                market_info = new_info
                _market_slug = market_info["slug"]
                _up_token_id = market_info["up_token_id"]
            last_discover = now

        if now - last_fetch >= fetch_interval:
            fetch_market_data(db, api, market_info)
            last_fetch = now
            run_strategies(db, strategy_mgr, simulator, _market_slug)
        time.sleep(5)

    logger.info("调度器已停止")


def main():
    global _running, _market_slug, _up_token_id

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

    # 发现 BTC updown 市场
    market_info = find_btc_market(api)
    if not market_info:
        logger.error("无法找到 BTC updown 市场，程序退出")
        return

    _market_slug = market_info["slug"]
    _up_token_id = market_info["up_token_id"]

    strategy_mgr = StrategyManager(db, api)
    strategy_mgr.sync_strategies_to_db()
    logger.info("策略管理器初始化完成")

    acc_mgr = AccountManager(db)
    acc_mgr.load_accounts()

    simulator = TradingSimulator(db, acc_mgr, config.ACCOUNT_DEFAULTS)

    # 首次获取数据
    fetch_market_data(db, api, market_info)

    scheduler_thread = threading.Thread(
        target=scheduler_loop,
        args=(db, api, strategy_mgr, simulator, market_info),
        daemon=True, name="Scheduler"
    )
    scheduler_thread.start()
    logger.info("调度器线程已启动")

    app = create_app(
        db_manager=db,
        strategy_manager=strategy_mgr,
        simulator=simulator,
        polymarket_api=api,
        market_id=_market_slug,
        market_slug=_market_slug,
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
