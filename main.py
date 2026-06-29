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
from src.api.btc_price import BTCPriceTracker, get_btc_klines
from src.strategy.strategy_manager import StrategyManager
from src.strategy.strategy_loader import discover_strategies
from src.trading.account import AccountManager
from src.trading.simulator import TradingSimulator
from src.web.app import create_app

_running = True
_current_price = 0.0
_market_slug = ""
_event_slug = ""
_up_token_id = ""
_down_token_id = ""
_btc_tracker = BTCPriceTracker()
_btc_price = 0.0


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

    # 同步 BTC 价格追踪器到新窗口
    try:
        from src.api.polymarket_api import _parse_iso_time
        start_dt = _parse_iso_time(info["start_time"])
        if start_dt:
            window_ts = int(start_dt.timestamp())
            _btc_tracker.sync_with_window(window_ts)
    except Exception:
        pass

    return info


def fetch_market_data(db, api, market_info):
    global _current_price, _btc_price
    logger = get_logger()

    if not market_info:
        return

    try:
        slug = market_info["slug"]
        up_id = market_info["up_token_id"]

        # 从 CLOB /price 获取 Polymarket 价格（含 bid/ask）
        pm_price_data = api.get_realtime_price(up_id)
        mid_price = pm_price_data.get("mid", 0)
        bid = pm_price_data.get("bid", 0)
        ask = pm_price_data.get("ask", 0)

        if mid_price <= 0:
            # fallback: 只用 get_current_price
            mid_price = api.get_current_price(up_id)

        if mid_price > 0:
            _current_price = mid_price
            now = int(time.time())
            kline = [{"timestamp": now, "open": mid_price, "high": mid_price,
                       "low": mid_price, "close": mid_price, "volume": 0}]
            db.insert_market_data(slug, kline)
            logger.info(f"Polymarket Up: {mid_price:.4f} (bid={bid:.4f} ask={ask:.4f})")
        else:
            logger.warning(f"获取Polymarket价格失败 (token={up_id[:30]}...)")


        try:
            # 同步到 Web
            app = __import__('sys').modules.get('flask', None)
        except Exception:
            pass

        # 获取 BTC 真实价格并存入数据库（策略用）
        _btc_tracker.update()
        btc_price = _btc_tracker.current_price
        if btc_price > 0:
            _btc_price = btc_price
            now = int(time.time())
            btc_kline = [{"timestamp": now, "open": btc_price, "high": btc_price,
                          "low": btc_price, "close": btc_price, "volume": 0}]
            db.insert_market_data("BTCUSDT", btc_kline)

            direction = _btc_tracker.direction
            change = _btc_tracker.change_pct
            logger.info(f"BTC真实价格: ${btc_price:.2f} | "
                        f"窗口方向: {direction} ({change:+.3f}%) | "
                        f"Up token: {_current_price:.4f}")

    except Exception as e:
        logger.error(f"获取市场数据失败: {e}")


def run_strategies(db, strategy_mgr, simulator, market_slug):
    global _btc_tracker
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
    global _running, _market_slug, _event_slug, _up_token_id, _down_token_id, _btc_tracker
    logger = get_logger()
    fetch_interval = config.BTC_MARKET.get("fetch_interval", 60)
    rediscover_interval = 120  # 2分钟重发现（15分钟窗口市场快速切换）
    last_fetch = 0
    last_discover = 0
    logger.info(f"调度器启动 | 数据获取间隔: {fetch_interval}s | 市场重发现间隔: {rediscover_interval}s")

    while _running:
        now = time.time()

        if now - last_discover >= rediscover_interval:
            new_info = find_btc_market(api)
            if new_info:
                market_info = new_info
                _market_slug = market_info["slug"]
                _event_slug = market_info.get("event_slug", market_info["slug"])
                _up_token_id = market_info["up_token_id"]
                _down_token_id = market_info.get("down_token_id", "")
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

    _market_slug = market_info["slug"]           # 市场 slug（DB 存储用）
    _event_slug = market_info.get("event_slug", market_info["slug"])  # 事件 slug（URL 用）
    _up_token_id = market_info["up_token_id"]
    _down_token_id = market_info.get("down_token_id", "")

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
        market_slug=_event_slug,  # 前端展示用事件URL
    )

    # Web 数据同步线程
    def sync_data_to_web():
        global _running, _btc_tracker, _current_price
        _bid = 0.0
        _ask = 0.0
        while _running:
            try:
                # 获取实时的 bid/ask
                if _up_token_id:
                    pm_data = api.get_realtime_price(_up_token_id)
                    _bid = pm_data.get("bid", 0)
                    _ask = pm_data.get("ask", 0)
                app.update_pm_price(_current_price, _bid, _ask)
                app.update_btc_info(
                    _btc_tracker.current_price,
                    _btc_tracker.direction,
                    _btc_tracker.change_pct
                )
            except Exception:
                pass
            time.sleep(5)

    sync_thread = threading.Thread(target=sync_data_to_web, daemon=True, name="WebSync")
    sync_thread.start()

    logger.info(f"Web Dashboard 启动: http://{config.WEB['host']}:{config.WEB['port']}")
    app.run(
        host=config.WEB["host"],
        port=config.WEB["port"],
        debug=config.WEB["debug"],
        use_reloader=False,
    )


if __name__ == "__main__":
    main()
