import logging
import os
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

from grpc_helper.static_config import RpcStaticConfig


def add_rotating_handler(log_folder: Path, logger: logging.Logger):
    # Configure persisting folder/file for logs
    log_file = log_folder / logger.name / f"{logger.name}.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)

    # Setup rotating handler with specific format
    handler = TimedRotatingFileHandler(
        log_file,
        when=RpcStaticConfig.LOGS_ROLLOVER_INTERVAL_UNIT.str_val,
        interval=RpcStaticConfig.LOGS_ROLLOVER_INTERVAL.int_val,
        backupCount=RpcStaticConfig.LOGS_BACKUP.int_val,
    )
    handler.setFormatter(
        logging.Formatter(
            f"%(asctime)s.%(msecs)03d [%(threadName)s{'/%(name)s' if isinstance(logger,logging.RootLogger) else ''}] %(levelname)s %(message)s - %(filename)s:%(funcName)s:%(lineno)d",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    logger.addHandler(handler)

    # First log!
    logger.info(f"---------- New {logger.name} logger instance for process {os.getpid()} ----------")


def clean_rotating_handler(logger):
    # Remove any rotating handler
    logger.info("Closing file log (shutting down)")
    for handler in filter(lambda h: isinstance(h, TimedRotatingFileHandler), logger.handlers):
        logger.removeHandler(handler)
