import json
import logging
from abc import ABC, abstractmethod
from pathlib import Path
from threading import RLock
from typing import Callable, List, Set, Tuple

from grpc_helper.api import EventApiVersion, Filter, ProxyRegisterRequest, ResultCode, ServerApiVersion
from grpc_helper.api.events_pb2_grpc import EventServiceStub
from grpc_helper.api.server_pb2_grpc import RpcServerServiceStub
from grpc_helper.client import RpcClient
from grpc_helper.errors import RpcException
from grpc_helper.folders import Folders
from grpc_helper.logs.logs_utils import add_rotating_handler
from grpc_helper.static_config import RpcStaticConfig
from grpc_helper.utils import get_current_ip


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

    def _init_folders_n_logger(self, folders: Folders, port: int):
        # Remember folders and port
        self.folders = folders if folders is not None else Folders()
        self.port = port

        # Prepare handler
        add_rotating_handler(self._log_folder, self.logger)

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

    @property
    def _proxied_servers(self) -> Set[Tuple[str, int]]:
        # Tuples of remote RPC server host,port for each registered proxied service
        proxied_servers = set()
        for service_info in filter(lambda si: si.is_proxy and si.proxy_port > 0, self.client.srv.info(Filter()).items):
            proxied_servers.add((service_info.proxy_host if len(service_info.proxy_host) else RpcStaticConfig.MAIN_HOST.str_val, service_info.proxy_port))
        return proxied_servers

    def _proxied_clients(self, stubs_map: dict) -> List[RpcClient]:
        # Map proxied servers to clients
        out = []
        for host, port in self._proxied_servers:
            out.append(RpcClient(host, port, stubs_map, name=type(self).__name__, timeout=RpcStaticConfig.CLIENT_TIMEOUT.float_val, logger=self.logger))
        return out


class RpcProxiedManager(RpcManager, ABC):
    def __init__(self, config_name: str = None, config_validator: Callable = None):
        super().__init__(config_name=config_name, config_validator=config_validator)
        self.proxy_client = None

    def _proxy_stubs(self) -> dict:
        """
        Dict of stubs to be added to proxy_client
        """
        return {}

    @abstractmethod
    def _proxied_services(self) -> List[str]:  # pragma: no cover
        """
        List of service names to be proxied
        """
        pass

    @abstractmethod
    def _proxied_version(self) -> str:  # pragma: no cover
        """
        Version of proxied service
        """
        pass

    @property
    def _proxy_use_current_host(self) -> bool:
        """
        States if proxy registration should use current host IP or not (default)
        """
        return False

    def _load(self):
        # Prepare proxy client
        stubs_map = {"srv": (RpcServerServiceStub, ServerApiVersion.SERVER_API_CURRENT), "events": (EventServiceStub, EventApiVersion.EVENT_API_CURRENT)}
        stubs_map.update(self._proxy_stubs())
        client_candidate = RpcClient(
            RpcStaticConfig.MAIN_HOST.str_val, RpcStaticConfig.MAIN_PORT.int_val, stubs_map, timeout=RpcStaticConfig.CLIENT_TIMEOUT.float_val
        )

        # Register in proxy
        client_candidate.srv.proxy_register(
            ProxyRegisterRequest(
                names=self._proxied_services(),
                version=self._proxied_version(),
                host=self.__current_ip if self._proxy_use_current_host else RpcStaticConfig.MAIN_HOST.str_val,
                port=self.port,
            )
        )
        self.proxy_client = client_candidate

    @property
    def __current_ip(self):
        return get_current_ip(RpcStaticConfig.MAIN_HOST.str_val)

    def _shutdown(self):
        # Forget from proxy (if it was registered)
        if self.proxy_client is not None:
            self.proxy_client.srv.proxy_forget(Filter(names=self._proxied_services()))
