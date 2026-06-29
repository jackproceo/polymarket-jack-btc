"""
配置文件 - Polymarket BTC 套利模拟程序
"""
import os

# 项目根目录
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
LOGS_DIR = os.path.join(BASE_DIR, "logs")

# SQLite 数据库路径
DB_PATH = os.path.join(DATA_DIR, "trades.db")

# Polymarket API 地址
POLYMARKET = {
    "gamma_api": "https://gamma-api.polymarket.com",
    "clob_api": "https://clob.polymarket.com",
}

# BTC 市场配置（程序启动时会自动从 API 获取，这里为备用配置）
BTC_MARKET = {
    "keyword": "BTC",
    "interval": "15m",       # K线间隔
    "limit": 200,            # 每次获取K线数量
    "fetch_interval": 60,    # 数据获取间隔（秒）
}

# 模拟账户默认配置
ACCOUNT_DEFAULTS = {
    "initial_balance": 1000.0,   # 初始余额（USDC）
    "fee_rate": 0.005,           # 手续费率 0.5%
    "slippage": 0.002,          # 滑点 0.2%
    "trade_amount": 50.0,        # 每次交易固定金额（USDC）
}

# 策略默认参数
STRATEGY_DEFAULTS = {
    "MACD": {
        "fast_period": 12,
        "slow_period": 26,
        "signal_period": 9,
        "buy_threshold": 0.0,    # MACD上穿信号线触发买入
        "sell_threshold": 0.0,    # MACD下穿信号线触发卖出
    },
    "MA": {
        "short_period": 20,
        "long_period": 60,
        "ma_type": "EMA",          # SMA / EMA
    },
}

# Web 服务配置
WEB = {
    "host": "0.0.0.0",
    "port": 5000,
    "debug": False,
    "poll_interval": 5000,    # 前端轮询间隔（毫秒）
}

# 日志配置
LOGGING = {
    "level": "INFO",
    "file": os.path.join(LOGS_DIR, "bot.log"),
    "max_bytes": 10 * 1024 * 1024,  # 10MB
    "backup_count": 5,
    "format": "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
}
