"""
程序入口 - Polymarket BTC 套利模拟程序
"""
import os
import re
import sys
import json
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
    """查找 btc-updown 事件中 BTC 上涨市场
    返回: (event_slug, market_condition_id, up_token_id)
    
    Polymarket 上的 BTC 交易市场是 btc-updown-15m 事件，
    URL 格式: https://polymarket.com/zh/event/btc-updown-15m-{timestamp}
    
    策略：根据当前时间构造 btc-updown-15m-{ts} slug，
    直接通过 Gamma API 获取事件详情，提取 Up 市场 token。
    """
    logger = get_logger()

    # 直接按时间构造 slug 并获取事件（不依赖关键词搜索）
    events = api.search_btc_updown_events(limit=7)

    if not events:
        logger.error("未找到任何 btc-updown 事件，请检查 Polymarket 是否有活跃的 BTC 市场")
        return "", "", ""

    # 遍历事件，从markets中提取 Up 市场的 token
    for evt in events:
        event_slug = evt.get("slug", "")
        markets = evt.get("markets", [])

        if not markets:
            continue

        # btc-updown 事件通常有2个市场: "Up" 和 "Down"
        best_condition_id = ""
        best_token_id = ""

        for mkt in markets:
            question = mkt.get("question", mkt.get("title", "")).lower()
            if not question:
                continue

            # 找 "up" 市场
            if "up" in question or "上涨" in question or "rise" in question:
                condition_id = mkt.get("conditionId", mkt.get("condition_id", ""))
                clob_ids = mkt.get("clobTokenIds", mkt.get("clob_token_ids", ""))

                # 解析 clobTokenIds（可能是 JSON 字符串数组）
                if isinstance(clob_ids, str) and clob_ids:
                    try:
                        clob_ids = json.loads(clob_ids)
                    except json.JSONDecodeError:
                        clob_ids = []

                tokens = mkt.get("tokens", [])
                if not clob_ids and tokens:
                    for t in tokens:
                        outcome = t.get("outcome", "").upper()
                        if outcome in ("YES", "UP"):
                            best_token_id = t.get("token_id", t.get("tokenId", ""))
                            break
                elif clob_ids:
                    # clobTokenIds[0] 通常是 Up/YES token
                    if isinstance(clob_ids, list) and len(clob_ids) > 0:
                        best_token_id = clob_ids[0]
                    else:
                        best_token_id = str(clob_ids)

                best_condition_id = condition_id

                if best_condition_id and best_token_id:
                    logger.info(f"找到 BTC updown 事件: {event_slug}")
                    logger.info(f"  Up 市场: {question}")
                    logger.info(f"  condition_id: {best_condition_id[:30]}...")
                    logger.info(f"  token_id: {best_token_id[:30]}...")
                    return event_slug, best_condition_id, best_token_id

        # 如果没有 up/down 区分，取第一个市场
        if markets and not best_condition_id:
            mkt = markets[0]
            condition_id = mkt.get("conditionId", mkt.get("condition_id", ""))
            clob_ids = mkt.get("clobTokenIds", "")
            if isinstance(clob_ids, str) and clob_ids:
                try:
                    clob_ids = json.loads(clob_ids)
                    token_id = clob_ids[0] if isinstance(clob_ids, list) and clob_ids else ""
                except json.JSONDecodeError:
                    token_id = ""

            logger.info(f"使用第一个市场: event={event_slug}")
            return event_slug, condition_id, token_id

    logger.error("未找到合适的 BTC updown 市场 token")
    return "", "", ""


def fetch_market_data(db, api, market_slug, condition_id=None, btc_token_id=None):
    global _current_price, _btc_token_id, _market_condition_id
    logger = get_logger()

    try:
        if btc_token_id:
            _btc_token_id = btc_token_id
        if condition_id:
            _market_condition_id = condition_id

        tid = _btc_token_id
        if not tid:
            logger.warning("未找到 BTC token ID，跳过数据获取")
            return

        # 获取当前价格（CLOB API，内部自动标准化 token_id）
        price = api.get_current_price(tid)
        if price <= 0:
            logger.warning("获取价格失败")
            return

        _current_price = price

        # 将当前价格作为K线数据点存入数据库
        now = int(time.time())
        kline_data = [{
            "timestamp": now,
            "open": price,
            "high": price,
            "low": price,
            "close": price,
            "volume": 0,
        }]
        inserted = db.insert_market_data(market_slug, kline_data)
        if inserted > 0:
            logger.info(f"价格数据已记录: {price:.4f}")
        else:
            logger.debug(f"价格: {price:.4f} (数据点已存在)")

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
    # 标准化 token_id（clobTokenIds 可能返回 decimal，需转为 0x hex）
    yes_token_id = api._normalize_token_id(yes_token_id)
    condition_id = api._normalize_token_id(condition_id) if condition_id else ""
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
