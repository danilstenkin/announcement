from loguru import logger
import sys

logger.level("EVENT", no=25, color="<yellow>", icon="⚡")

logger.remove()

logger.add(
    sys.stdout,
    format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
            "<level>{level: <4}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
            "<level>{message}</level>",
    level="DEBUG",
    colorize=True,
)

logger.add(
    "logs/app.log",
    format="{time:YYYY-MM-DD HH:mm:ss} | {level: <4} | {name}:{function}:{line} | {message}",
    level="INFO",
    rotation="10 MB",    # Новый файл каждые 10 MB
    retention="30 days",  # Хранить логи 30 дней
    compression="zip",    # Старые логи сжимать
    encoding="utf-8",
)


def get_logger(name: str):
    return logger.bind(name=name)
