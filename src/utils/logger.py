"""
日志配置模块
"""
import logging
import os
from logging.handlers import RotatingFileHandler

def setup_logger(name="bot", log_file=None, level=logging.INFO,
                 max_bytes=10*1024*1024, backup_count=5):
    """配置并返回一个 logger"""
    logger = logging.getLogger(name)
    logger.setLevel(level)

    # 避免重复添加 handler
    if logger.handlers:
        return logger

    fmt = logging.Formatter(
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
