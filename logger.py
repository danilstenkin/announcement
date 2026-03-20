import logging
from logging.config import dictConfig

LOGGER_CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "default": {"format": "%(asctime)s - %(name)s - %(levelname)s - %(message)s"}
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "level": "INFO",
            "formatter": "default",
            "stream": "ext://sys.stdout",
        },
        "file": {
            "class": "logging.FileHandler",
            "level": "INFO",
            "formatter": "default",
            "filename": "logs/bot.log",
            "mode": "a",
        },
    },
    "loggers": {
        "chat_logger": {
            "level": "INFO",
            "handlers": ["console", "file"],
            "propagate": False,
        }
    },
}

dictConfig(LOGGER_CONFIG)

def get_logger(name: str = None):
    if name and name != "__main__":
        module = name.split(".")[-1]
        return logging.getLogger(f"chat_logger.{module}")
    return logging.getLogger("chat_logger")


logger = get_logger()