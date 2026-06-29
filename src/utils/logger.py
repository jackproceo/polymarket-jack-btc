"""
日志配置模块 - 使用中国时区 (UTC+8)
"""
import logging
import os
from datetime import datetime, timezone, timedelta
from logging.handlers import RotatingFileHandler

# 中国时区 UTC+8
CHINA_TZ = timezone(timedelta(hours=8))


class ChinaFormatter(logging.Formatter):
    """使用中国时区的日志格式化器"""

    def formatTime(self, record, datefmt=None):
        dt = datetime.fromtimestamp(record.created, tz=CHINA_TZ)
        if datefmt:
            return dt.strftime(datefmt)
        return dt.strftime("%Y-%m-%d %H:%M:%S")


def setup_logger(name="bot", log_file=None, level=logging.INFO,
                 max_bytes=10*1024*1024, backup_count=5):
    """配置并返回一个 logger（中国时区）"""
    logger = logging.getLogger(name)
    logger.setLevel(level)

    # 避免重复添加 handler
    if logger.handlers:
        return logger

    fmt = ChinaFormatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    # 控制台输出
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    logger.addHandler(console)

    # 文件输出（按大小滚动）
    if log_file:
        os.makedirs(os.path.dirname(log_file), exist_ok=True)
        file_handler = RotatingFileHandler(
            log_file, maxBytes=max_bytes,
            backupCount=backup_count, encoding="utf-8"
        )
        file_handler.setFormatter(fmt)
        logger.addHandler(file_handler)

    return logger


def get_logger(name="bot"):
    """获取已配置的 logger"""
    return logging.getLogger(name)
