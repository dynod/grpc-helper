import json
import logging
import os
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from threading import RLock
from typing import Callable

from grpc_helper.api import ResultCode
from grpc_helper.client import RpcClient
from grpc_helper.errors import RpcException
from grpc_helper.folders import Folders
from grpc_helper.static_config import RpcStaticConfig


class RpcManager:
    """
    Shared interface for all RPC managers, helping to control the service lifecycle.
    """

    def __init__(self, config_name: str = None, config_validator: Callable = None):
        # Prepare some shared attributes
        self.lock = RLock()
        self.client = None
        self.folders = None
        self.logger = logging.getLogger(type(self).__name__)
        self.config_name = config_name
        self.config_validator = config_validator

    @property
    def _log_folder(self) -> Path:
        log_subfolder = Path(RpcStaticConfig.LOGS_FOLDER.str_val)
        log_folder = self.folders.workspace / log_subfolder if self.folders.workspace is not None else log_subfolder
        log_folder.parent.mkdir(parents=True, exist_ok=True)
        return log_folder

    def _add_rotating_handler(self, logger: logging.Logger):
        # Configure persisting folder/file for logs
        log_file = self._log_folder / logger.name / f"{logger.name}.log"
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
                f"%(asctime)s.%(msecs)03d [%(process)d{'/%(name)s' if isinstance(logger,logging.RootLogger) else ''}] %(levelname)s %(message)s - %(filename)s:%(funcName)s:%(lineno)d",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        logger.addHandler(handler)

        # First log!
        logger.info(f"---------- New {logger.name} logger instance for process {os.getpid()} ----------")

    def _clean_rotating_handler(self, logger):
        # Remove any rotating handler
        logger.info("Closing file log (shutting down)")
        for handler in filter(lambda h: isinstance(h, TimedRotatingFileHandler), logger.handlers):
            logger.removeHandler(handler)

    def _init_folders_n_logger(self, folders: Folders):
        # Remember folders
        self.folders = folders if folders is not None else Folders()

        # Prepare handler
        self._add_rotating_handler(self.logger)

    def _preload(self, client: RpcClient, folders: Folders):
        # Remember client, and delegate loading to subclass
        self.client = client
        self._load()

    def _load(self):
        """
        To be defined by sub-classes, if some specific operations have to be performed once all basic initializations are done
        """
        pass

    def _shutdown(self):
        """
        To be defined by sub-classes, if some specific operations have to be performed before shutting down the process
        """

    def _load_config(self, config_folder: Path) -> dict:
        # Check config file presence
        if config_folder is not None and self.config_name is not None:
            config_file = config_folder / self.config_name
            if config_file.is_file():
                with config_file.open("r") as f:
                    # Load and verify model from json file
                    try:
                        json_model = json.load(f)
                    except json.JSONDecodeError as e:
                        raise RpcException(f"Invalid config json file (bad json: {e}): {config_file}", ResultCode.ERROR_MODEL_INVALID)

                    # Also validate model with provided validator
                    if self.config_validator is not None:
                        self.config_validator(config_file, json_model)

                    # Model looks to be valid: go on
                    return json_model

        # Default model
        return {}

    def _save_config(self, config: dict):
        # Save config file to workspace (if defined)
        folder = self.folders.workspace
        if folder is not None:
            folder.mkdir(parents=True, exist_ok=True)
            config_file = folder / self.config_name
            with config_file.open("w") as f:
                json.dump(config, f, indent=4)
        else:
            self.logger.warn("No workspace defined; skip config persistence")
