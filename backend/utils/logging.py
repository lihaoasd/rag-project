"""
集中日志配置 — 控制台 + 按大小轮转文件输出。

用法：
    from utils.logging import setup_logging, get_logger

    # 应用启动时调用一次
    setup_logging(level="INFO", log_dir="./logs")

    # 各模块获取 logger
    logger = get_logger(__name__)
    logger.info("消息")
"""
import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

# 默认格式：时间 | 级别 | 模块 | 消息
LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# 第三方库日志级别控制
THIRD_PARTY_LOG_LEVELS = {
    "sqlalchemy.engine": logging.WARNING,
    "sqlalchemy.pool": logging.WARNING,
    "llama_index": logging.INFO,
    "httpx": logging.WARNING,
    "httpcore": logging.WARNING,
    "urllib3": logging.WARNING,
    "uvicorn": logging.WARNING,
    "uvicorn.access": logging.WARNING,
}


def setup_logging(level: str = "INFO", log_dir: str = "./logs") -> None:
    """初始化日志系统 — 控制台 + 文件双输出。

    应在应用启动时调用一次。重复调用不会重复添加 handler。
    """
    root = logging.getLogger()
    root.setLevel(_resolve_level(level))

    # 避免重复初始化
    if root.handlers:
        return

    # 控制台 handler — 输出到 stderr
    console = logging.StreamHandler(sys.stderr)
    console.setLevel(_resolve_level(level))
    console.setFormatter(logging.Formatter(LOG_FORMAT, DATE_FORMAT))
    root.addHandler(console)

    # 文件 handler — 按大小轮转，保留最近 5 个文件
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)

    file_handler = RotatingFileHandler(
        log_path / "app.log",
        maxBytes=10 * 1024 * 1024,  # 10 MB
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)  # 文件记录所有级别
    file_handler.setFormatter(logging.Formatter(LOG_FORMAT, DATE_FORMAT))
    root.addHandler(file_handler)

    # 控制第三方库日志级别
    for logger_name, logger_level in THIRD_PARTY_LOG_LEVELS.items():
        logging.getLogger(logger_name).setLevel(logger_level)

    root.info("日志系统初始化完成 (level=%s, dir=%s)", level, str(log_path.resolve()))


def get_logger(name: str) -> logging.Logger:
    """获取指定名称的 logger。

    自动截取模块路径最后两段作为名称（如 routers.knowledge_base）。
    """
    parts = name.split(".")
    short_name = ".".join(parts[-2:]) if len(parts) >= 2 else name
    return logging.getLogger(short_name)


def _resolve_level(level: str) -> int:
    """将字符串日志级别转换为 logging 常量。"""
    return getattr(logging, level.upper(), logging.INFO)
