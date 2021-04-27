import faulthandler
import inspect
import logging
import time
import traceback
from concurrent import futures
from dataclasses import dataclass
from signal import SIGUSR2, signal
from threading import Thread, current_thread
from types import ModuleType
from typing import Callable, Dict, List, NoReturn, TypeVar, Union

from google.protobuf.internal.enum_type_wrapper import EnumTypeWrapper
from grpc import Server, server

import grpc_helper
from grpc_helper.api import (
    ConfigApiVersion,
    Filter,
    LoggerApiVersion,
    MultiServiceInfo,
    ProxyRegisterRequest,
    Result,
    ResultCode,
    ResultStatus,
    ServerApiVersion,
    ServiceInfo,
    ShutdownRequest,
)
from grpc_helper.api.config_pb2_grpc import ConfigServiceStub, add_ConfigServiceServicer_to_server
from grpc_helper.api.logger_pb2_grpc import LoggerServiceStub, add_LoggerServiceServicer_to_server
from grpc_helper.api.server_pb2_grpc import RpcServerServiceServicer, RpcServerServiceStub, add_RpcServerServiceServicer_to_server
from grpc_helper.client import RpcClient
from grpc_helper.config.cfg_item import Config
from grpc_helper.config.cfg_manager import ConfigManager
from grpc_helper.errors import RpcException
from grpc_helper.folders import Folders
from grpc_helper.logs.logs_manager import LogsManager
from grpc_helper.manager import RpcManager
from grpc_helper.static_config import RpcStaticConfig
from grpc_helper.trace import trace_buffer, trace_rpc

# Config file name
PROXY_FILE = "proxy.json"


# Persisted model keys
class ProxyModel:
    VERSION = "version"
    HOST = "host"
    PORT = "port"


class RpcMethod:
    def __init__(self, name: str, manager: RpcManager, stub: object, return_type: object, info: ServiceInfo, server: object):
        self.manager_method = getattr(manager, name) if manager is not None else None
        self.name = name
        self.logger = manager.logger if manager is not None else logging.getLogger("RpcServer")
        self.return_type = return_type
        self.info = info
        self.server = server
        self.stub = stub

    def __call__(self, request, context):
        # Remember call for debug dump
        input_trace = trace_rpc(True, request, context=context)
        logged_call = f"Thread 0x{current_thread().ident:016x} -- " + input_trace
        with self.server.lock:
            self.server.calls.append(logged_call)
        self.logger.debug(input_trace)

        try:
            # Verify API version
            metadata = {k: v for k, v in context.invocation_metadata()}
            if "api_version" in metadata:
                client_version = int(metadata["api_version"])
                if client_version > self.info.current_api_version:
                    raise RpcException(
                        f"Server current API version ({self.info.current_api_version}) is too old for client API version ({client_version})",
                        rc=ResultCode.ERROR_API_SERVER_TOO_OLD,
                    )
                elif client_version < self.info.supported_api_version:
                    raise RpcException(
                        f"Client API version ({client_version}) is too old for server supported API version ({self.info.current_api_version})",
                        rc=ResultCode.ERROR_API_CLIENT_TOO_OLD,
                    )

            # Proxy?
            if self.info.is_proxy:
                # Delegate to remote proxy, if port is set
                first_try = time.time()
                while self.info.proxy_port == 0:
                    # Wait a bit more for proxy registration
                    if (time.time() - first_try) < RpcStaticConfig.CLIENT_TIMEOUT.float_val:
                        # Server is not available, and timeout didn't expired yet: sleep and retry
                        time.sleep(0.5)
                        self.logger.debug(f"Proxy not registered yet for method {self.name}: wait a bit...")
                    else:
                        # Timeout expired... notify the caller
                        raise RpcException("Proxy didn't registered in time for method call", ResultCode.ERROR_PROXY_UNREGISTERED)

                # Temporary client to proxied server (that won't raise exceptions: let's simply forward the output message to final client)
                client = RpcClient(
                    self.info.proxy_host if len(self.info.proxy_host) else RpcStaticConfig.MAIN_HOST.str_val,
                    self.info.proxy_port,
                    {"stub": (self.stub, self.info.current_api_version)},
                    name="proxy",
                    timeout=RpcStaticConfig.CLIENT_TIMEOUT.float_val,
                    logger=self.logger,
                    exception=False,
                )

                # Call remote method
                result = getattr(client.stub, self.name)(request)
            else:
                # Not a proxy method, delegate to manager
                result = self.manager_method(request)

        except Exception as e:
            # Something happened during the RPC execution
            stack = "".join(traceback.format_tb(e.__traceback__))
            self.logger.error(f"Exception occurred: {e}\n{stack}")

            # Extract RC if this was a known error
            rc = e.rc if isinstance(e, RpcException) else ResultCode.ERROR

            # Special case if operation just returns a Result object
            r = Result(code=rc, msg=str(e), stack=stack)
            result = ResultStatus(r=r) if self.return_type is None else self.return_type(r=r)

        # Forget call from debug dump
        with self.server.lock:
            self.server.calls.remove(logged_call)
        self.logger.debug(trace_rpc(False, result, context=context))
        return result


class RpcServicer:
    """
    Generic servicer implementation, that:
     * logs method calls
     * checks api version
     * routes method to provided manager
    """

    def __init__(self, manager: RpcManager, stub: object, info: ServiceInfo, server: object):
        # Filter on manager methods
        logger = manager.logger if not info.is_proxy else logging.getLogger("RpcServer")
        logger.debug(f"Initializing RPC servicer for {info.name}")
        for n in filter(lambda x: not x.startswith("_") and callable(getattr(manager, x)), dir(manager)):
            method = getattr(manager, n)
            sig = inspect.signature(method)
            return_type = sig.return_annotation
            # Only methods with declared return type + one input parameter (and we're not registering a proxy)
            if return_type != inspect._empty and len(sig.parameters) == 1 and not info.is_proxy:
                if return_type == Result:  # pragma: no cover
                    raise RpcException(f"Can't declare {n} rpc with Result as a return type; please use Return Status instead", ResultCode.ERROR_PARAM_INVALID)
                logger.debug(f" >> add method {n} (returns {return_type.__name__})")
                setattr(self, n, RpcMethod(n, manager, None, return_type, info, server))
            # Or methods coming from the parent stub
            elif return_type == inspect._empty and len(sig.parameters) == 2 and all(p in ["request", "context"] for p in sig.parameters.keys()):
                if info.is_proxy:
                    # Register proxy method
                    logger.debug(f" >> add proxy method {n}")
                    setattr(self, n, RpcMethod(n, None, stub, None, info, server))
                else:
                    # Register stub method (not defined in the manager - will raise an exception on runtime)
                    logger.debug(f" >> add stub method {n}")
                    setattr(self, n, method)


@dataclass
class RpcServiceDescriptor:
    """
    Data class describing a service to be served by the RpcServer class

    Attributes:
        module: python module providing the service
        name: service name
        api_version: enum class holding the supported/current API versions
        manager: object instance to which delegating services requests; may be None for proxy services
        register_method: GRPC-generated method to be called for service registration on the server instance
        client_stub: GRPC-generated class holding the service client stub
        is_proxy: States if this service is a proxied one (to be forwarded to a remote RPC server)
    """

    module: ModuleType
    name: str
    api_version: EnumTypeWrapper
    manager: RpcManager
    register_method: Callable[[object, object], NoReturn]
    client_stub: TypeVar
    is_proxy: bool = False


class RpcServer(RpcServerServiceServicer, RpcManager):
    """
    Wrapper to GRPC api to setup an RPC server.

    Constructor arguments:
        port:
            TCP port to be listened by the RPC server (mandatory).
        descriptors:
            list of service RpcServiceDescriptor instances (mandatory).
        folders:
            Folders class instance, holding the different level folders (default: None).
        cli_config:
            dict of string:string config item defaults provided on the command line (default: None).
        static_items:
            list of Config/ConfigHolder instances for static config items (default: None).
        user_items:
            list of Config/ConfigHolder instances for user config items (default: None).
    """

    def __init__(
        self,
        port: int,
        descriptors: List[RpcServiceDescriptor],
        folders: Folders = None,
        cli_config: Dict[str, str] = None,
        static_items: List[Config] = None,
        user_items: List[Config] = None,
    ):
        RpcManager.__init__(self, PROXY_FILE)
        self.__port = port
        self.calls = []
        self.__is_running = False

        # Prepare config manager
        # (by the way, add our own static items)
        config_m = ConfigManager(folders, cli_config, (static_items + [RpcStaticConfig]) if static_items is not None else [RpcStaticConfig], user_items)

        # Create server instance, disabling port reuse
        self.__server = server(futures.ThreadPoolExecutor(max_workers=RpcStaticConfig.MAX_WORKERS.int_val), options=[("grpc.so_reuseport", 0)])

        # Systematically add services:
        # - to handle server basic operations
        # - to handle remote user configuration update
        # - to handle remote loggers configuration update
        self.descriptors = [
            RpcServiceDescriptor(grpc_helper, "srv", ServerApiVersion, self, add_RpcServerServiceServicer_to_server, RpcServerServiceStub),
            RpcServiceDescriptor(grpc_helper, "config", ConfigApiVersion, config_m, add_ConfigServiceServicer_to_server, ConfigServiceStub),
            RpcServiceDescriptor(grpc_helper, "log", LoggerApiVersion, LogsManager(), add_LoggerServiceServicer_to_server, LoggerServiceStub),
        ]

        # Get servicers
        self.descriptors.extend(descriptors)

        # Load persisted proxies model
        proxies_model = self._load_config(folders.workspace)

        # Prepare auto clients stubs map
        stubs_map = {}

        # Register everything
        self.__info = {}
        for descriptor in self.descriptors:
            # Prepare folders and logger (only for non-proxy)
            if not descriptor.is_proxy:
                descriptor.manager._init_folders_n_logger(folders)

            # Persisted model?
            persisted_model = proxies_model[descriptor.name] if descriptor.name in proxies_model else None

            # Build info for service
            versions = list(descriptor.api_version.values())
            versions.remove(0)
            current_version = max(versions)
            info = ServiceInfo(
                name=descriptor.name,
                version=persisted_model[ProxyModel.VERSION]
                if persisted_model is not None
                else f"{descriptor.module.__title__}:{descriptor.module.__version__}",
                current_api_version=current_version,
                supported_api_version=min(versions),
                is_proxy=descriptor.is_proxy,
                proxy_port=persisted_model[ProxyModel.PORT] if persisted_model is not None else None,
                proxy_host=persisted_model[ProxyModel.HOST] if persisted_model is not None else None,
            )
            self.logger.debug(f"Registering service in RPC server: {trace_buffer(info)}")

            # Remember info
            self.__info[info.name] = info

            # Register servicer in RPC server
            descriptor.register_method(RpcServicer(descriptor.manager, descriptor.client_stub, info, self), self.__server)

            # Contribute to auto-client stubs map
            stubs_map[descriptor.name] = (descriptor.client_stub, current_version)

        # Setup port and start
        self.logger.debug(f"Starting RPC server on port {self.__port}")
        try:
            self.__server.add_insecure_port(f"[::]:{self.__port}")
        except Exception as e:  # NOQA: B902
            msg = f"Failed to start RPC server on port {self.__port}: {e}"
            self.logger.error(msg)
            raise RpcException(msg, rc=ResultCode.ERROR_PORT_BUSY)
        self.__server.start()
        self.__is_running = True
        self.logger.debug(f"RPC server started on port {self.__port}")

        # Hook debug signal
        signal(SIGUSR2, self.__dump_debug)

        # Load all managers (including client pointing to all served services)
        to_raise = None
        for descriptor in self.__real_descriptors:
            client = RpcClient(
                "localhost",
                self.__port,
                stubs_map,
                name=f"{descriptor.name}-client",
                timeout=RpcStaticConfig.CLIENT_TIMEOUT.float_val,
                logger=descriptor.manager.logger,
            )
            try:
                descriptor.manager._preload(client, folders)
            except Exception as e:
                self.logger.error(f"Error occurred during {descriptor.name} manager loading: {e}\n" + "".join(traceback.format_tb(e.__traceback__)))
                to_raise = e
                break

        # If something bad happened during managers loading, shutdown and raise exception
        if to_raise is not None:
            self.shutdown()
            raise to_raise

    @property
    def __real_descriptors(self):
        # Yield descriptors with a real manager registered (non-proxy ones)
        return filter(lambda d: not d.is_proxy, self.descriptors)

    def __dump_debug(self, signum, frame):
        """
        Dumps current threads + pending RPC requests
        """

        # Prepare debug output
        output = self._log_folder / f"RpcServerDump-{time.strftime('%Y%m%d%H%M%S')}.txt"
        with output.open("w") as f:
            # Dump threads
            faulthandler.dump_traceback(f, all_threads=True)

            # Dump pending calls
            f.write("\n\nPending RPC calls:\n")
            for call in list(self.calls):
                f.write(f"{call}\n")

    def shutdown(self, request: ShutdownRequest = None) -> ResultStatus:
        """
        Shuts down this RPC server instance
        """

        # Only if not shutdown yet
        if self.__server is not None:
            # Make sure we won't enter here twice
            terminating_server = self.__server
            self.__server = None

            # Stop server and wait for termination
            self.logger.debug(f"Shutting down RPC server on port {self.__port}; stop accepting new requests")
            terminating_server.stop(RpcStaticConfig.SHUTDOWN_GRACE.float_val)

            # Shutdown all managers
            self.logger.debug("Shutting down all managers")
            for descriptor in self.__real_descriptors:
                descriptor.manager._shutdown()

            # Is this an RPC call?
            if request is not None:
                # Yes --> delegate termination to its own thread, to return immediately from this request
                Thread(target=self.__finalize_shutdown, kwargs={"terminating_server": terminating_server, "request": request}, name="ShutdownThread").start()
            else:
                # Finalize shutdown immediately (internal call)
                self.__finalize_shutdown(terminating_server, None)

        # Always OK
        return ResultStatus()

    def __finalize_shutdown(self, terminating_server: Server, request: ShutdownRequest):
        # Wait for all pending requests to be terminated
        self.logger.debug("Waiting to terminate all requests")
        terminating_server.wait_for_termination()
        self.logger.debug(f"RPC server shut down on port {self.__port}")

        # Need to wait before real shutdown?
        # This may be useful to avoid being restarted by an orchestration manager (e.g. Docker Swarm), typically when doing a graceful shutdown before upgrade
        if request is not None and request.timeout >= 0:
            timeout = request.timeout if request.timeout > 0 else RpcStaticConfig.SHUTDOWN_TIMEOUT.int_val
            self.logger.warning(f"!!! Will shutdown in {timeout}s !!!")
            time.sleep(timeout)

        # Removing all rotating loggers
        for descriptor in self.__real_descriptors:
            self._clean_rotating_handler(descriptor.manager.logger)

        # Remove rotating handler for current + root loggers
        self._clean_rotating_handler(logging.getLogger())
        self.__is_running = False

    def info(self, request: Filter) -> MultiServiceInfo:
        # Verify input service names
        if len(request.names):
            services = self.__check_service_names(request)
        else:
            services = self.__info.values()

        # Just build message from stored info
        return MultiServiceInfo(items=services)

    def _init_folders_n_logger(self, folders: Folders):
        # Override to process root logger as well
        super()._init_folders_n_logger(folders)
        self._add_rotating_handler(logging.getLogger())

    @property
    def is_running(self) -> bool:
        # Just check is server is still running
        return self.__is_running

    def __check_service_names(self, request: Union[ProxyRegisterRequest, Filter]) -> List[ServiceInfo]:
        if any(n not in self.__info for n in request.names):
            raise RpcException("At least one of the required service names is unknown", ResultCode.ERROR_ITEM_UNKNOWN)
        return list(map(lambda n: self.__info[n], request.names))

    def __check_proxy_names(self, request: Union[ProxyRegisterRequest, Filter]) -> List[ServiceInfo]:
        # Verify input proxy names
        if len(request.names) == 0 or any(len(x) == 0 for x in request.names):
            raise RpcException("Missing input name in request", ResultCode.ERROR_PARAM_MISSING)
        services = self.__check_service_names(request)
        if any(not srv.is_proxy for srv in services):
            raise RpcException("At least one of the required services is not a proxy", ResultCode.ERROR_PARAM_INVALID)
        return services

    def __persist_proxies(self):
        # Save all configured proxies
        model = {}
        for name, srv_info in self.__info.items():
            if srv_info.is_proxy and srv_info.proxy_port > 0:
                model[name] = {ProxyModel.HOST: srv_info.proxy_host, ProxyModel.PORT: srv_info.proxy_port, ProxyModel.VERSION: srv_info.version}
        self._save_config(model)

    def proxy_register(self, request: ProxyRegisterRequest) -> ResultStatus:
        # Verify input params
        services = self.__check_proxy_names(request)
        if len(request.version) == 0 or request.port == 0:
            raise RpcException("Missing input parameter in request (one of version or port)", ResultCode.ERROR_PARAM_MISSING)

        # Ready to update service proxy info
        with self.lock:
            for srv in services:
                # Update info
                srv.version = request.version
                srv.proxy_port = request.port
                srv.proxy_host = request.host

            # Persist proxy info
            self.__persist_proxies()

        return ResultStatus()

    def proxy_forget(self, request: Filter) -> ResultStatus:
        # Verify input params
        services = self.__check_proxy_names(request)

        # Ready to update service proxy info
        with self.lock:
            for srv in services:
                # Update info
                srv.proxy_port = 0
                srv.proxy_host = ""

            # Persist proxy info
            self.__persist_proxies()

        return ResultStatus()
