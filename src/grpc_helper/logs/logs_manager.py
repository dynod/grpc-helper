import logging
from pathlib import Path
from typing import Iterable, Tuple, Union

from grpc_helper.api import Filter, LoggerConfig, LoggerLevel, LoggerStatus, LoggerUpdate, ResultCode
from grpc_helper.api.logger_pb2_grpc import LoggerServiceServicer
from grpc_helper.errors import RpcException
from grpc_helper.manager import RpcManager

# Loggers config file name
LOGGERS_FILE = "loggers.json"


class LogsManager(LoggerServiceServicer, RpcManager):
    """
    Loggers manager, and implementing the LoggerService API
    """

    def __init__(self):
        RpcManager.__init__(self, LOGGERS_FILE, self.__validate_config_file)

        # Remember reset level for root logger
        self._root_reset_level = logging.getLogger().level

    def __validate_config_file(self, config_file: Path, json_model):
        if not isinstance(json_model, dict) or any(not isinstance(v, str) and not isinstance(v, bool) for v in json_model.values()):
            raise RpcException(f"Invalid logger json file (expecting a simple str:[str or bool] object): {config_file}", ResultCode.ERROR_MODEL_INVALID)

    def get_logger(self, name: str) -> logging.Logger:
        return logging.getLogger(name if len(name) else None)

    def _load(self):
        # Load loggers configuration, layer per layer
        loggers = {}

        # 1. System level
        loggers.update(self._load_config(self.folders.system))
        self.logger.debug(f"Loading loggers config (from system folder at {self.folders.system}): {loggers}")

        # 2. User level
        loggers.update(self._load_config(self.folders.user))
        self.logger.debug(f"Loading loggers config (from user folder at {self.folders.system}): {loggers}")

        # 3. Workspace level
        loggers.update(self._load_config(self.folders.workspace))
        self.logger.debug(f"Loading loggers config (from workspace folder at {self.folders.system}): {loggers}")

        # Update loggers
        for name, level in loggers.items():
            logger = self.get_logger(name)
            if isinstance(level, bool):
                # Global enablement
                logger.disabled = not level
            else:
                # Level configuration: read from string
                try:
                    logger.setLevel(LoggerLevel.Value(f"LVL_{level}"))
                except ValueError:
                    self.logger.warn(f"Ignoring unknown level {level} for logger {name}")

    def __inner_level_to_api(self, inner_level: int) -> LoggerLevel:
        for v in LoggerLevel.values():
            if inner_level == v:
                return v
        # Probably a custom level; this is unknown by public API
        return LoggerLevel.LVL_UNKNOWN

    def __map_loggers(self, request: Union[Filter, LoggerUpdate]) -> Iterable[Tuple[logging.Logger, LoggerConfig]]:
        # Don't support empty request
        if (isinstance(request, Filter) and len(request.names) == 0) or (isinstance(request, LoggerUpdate) and len(request.items) == 0):
            raise RpcException("Empty request", rc=ResultCode.ERROR_PARAM_MISSING)

        # Map to a tuple of (logger, config)
        if isinstance(request, Filter):
            # Provide reset config (enabled + default level)
            return map(
                lambda n: (self.get_logger(n), LoggerConfig(name=n, enabled=True, level=self._root_reset_level if n == "" else LoggerLevel.LVL_UNKNOWN)),
                request.names,
            )
        else:
            return map(lambda r: (self.get_logger(r.name), r), request.items)

    def __map_logger_config(self, logger: logging.Logger) -> LoggerConfig:
        return LoggerConfig(name=logger.name, enabled=not logger.disabled, level=self.__inner_level_to_api(logger.level))

    def get(self, request: Filter) -> LoggerStatus:
        with self.lock:
            # Get all loggers status
            out = LoggerStatus()
            for logger, _useless in self.__map_loggers(request):
                out.items.append(self.__map_logger_config(logger))
        return out

    def __generic_set(self, request: Union[Filter, LoggerUpdate], persist: bool):
        with self.lock:
            # Update all required loggers to provided configuration
            out = LoggerStatus()
            config = self._load_config(self.folders.workspace)
            config_updated = False
            for logger, l_config in self.__map_loggers(request):
                # Update logger from parameters
                logger.setLevel(l_config.level)
                logger.disabled = not l_config.enabled

                # Load config, ready to be updated
                persisted_name = "" if isinstance(logger, logging.RootLogger) else logger.name
                if persist:
                    # Persist new configuration
                    config[persisted_name] = False if logger.disabled else LoggerLevel.Name(logger.level).replace("LVL_", "")
                    config_updated = True
                elif persisted_name in config:
                    # Something was persisted, and has now to be removed
                    del config[persisted_name]
                    config_updated = True

                # Update output status
                out.items.append(self.__map_logger_config(logger))

            # Update configuration one time after loop (if something was modified)
            if config_updated:
                self._save_config(config)
        return out

    def set(self, request: LoggerUpdate) -> LoggerStatus:  # NOQA: A003
        return self.__generic_set(request, True)

    def reset(self, request: Filter) -> LoggerStatus:
        return self.__generic_set(request, False)
