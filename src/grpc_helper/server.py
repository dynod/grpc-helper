import faulthandler
import inspect
import logging
import time
import traceback
from concurrent import futures
from dataclasses import dataclass
from pathlib import Path
from signal import SIGUSR2, signal
from threading import current_thread
from types import ModuleType
from typing import Callable, Dict, List, NoReturn, TypeVar

from google.protobuf.internal.enum_type_wrapper import EnumTypeWrapper
from grpc import server

import grpc_helper
from grpc_helper.api import ConfigApiVersion, Empty, InfoApiVersion, LoggerApiVersion, MultiServiceInfo, Result, ResultCode, ServiceInfo
from grpc_helper.api.config_pb2_grpc import ConfigServiceStub, add_ConfigServiceServicer_to_server
from grpc_helper.api.info_pb2_grpc import InfoServiceServicer, InfoServiceStub, add_InfoServiceServicer_to_server
from grpc_helper.api.logger_pb2_grpc import LoggerServiceStub, add_LoggerServiceServicer_to_server
from grpc_helper.client import RpcClient
from grpc_helper.config.cfg_item import Config
from grpc_helper.config.cfg_manager import ConfigManager
from grpc_helper.errors import RpcException
from grpc_helper.folders import Folders
from grpc_helper.logs.logs_manager import LogsManager
from grpc_helper.manager import RpcManager
from grpc_helper.static_config import RpcStaticConfig
from grpc_helper.trace import trace_buffer, trace_rpc


class RpcMethod:
    def __init__(self, name: str, manager: RpcManager, return_type: object, info: ServiceInfo, server: object):
        self.manager_method = getattr(manager, name)
        self.logger = manager.logger
        self.return_type = return_type
        self.info = info
        self.server = server

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

            # Ok, delegate to manager
            result = self.manager_method(request)

        except Exception as e:
            # Something happened during the RPC execution
            stack = "".join(traceback.format_tb(e.__traceback__))
            self.logger.error(f"Exception occurred: {e}\n{stack}")

            # Extract RC if this was a known error
            rc = e.rc if isinstance(e, RpcException) else ResultCode.ERROR

            # Special case if operation just returns a Result object
            r = Result(code=rc, msg=str(e), stack=stack)
            result = r if self.return_type == Result else self.return_type(r=r)

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

    def __init__(self, manager: RpcManager, info: ServiceInfo, server: object):
        # Filter on manager methods
        manager.logger.debug(f"Initializing RPC servicer for {info.name}")
        for n in filter(lambda x: not x.startswith("_") and callable(getattr(manager, x)), dir(manager)):
            method = getattr(manager, n)
            sig = inspect.signature(method)
            return_type = sig.return_annotation
            # Only methods with declared return type + one input parameter
            if return_type != inspect._empty and len(sig.parameters) == 1:
                manager.logger.debug(f" >> add method {n} (returns {return_type.__name__})")
                setattr(self, n, RpcMethod(n, manager, return_type, info, server))
            # Or methods coming from the parent stub
            elif return_type == inspect._empty and len(sig.parameters) == 2 and all(p in ["request", "context"] for p in sig.parameters.keys()):
                manager.logger.debug(f" >> add stub method {n}")
                setattr(self, n, method)


@dataclass
class RpcServiceDescriptor:
    """
    Data class describing a service to be served by the RpcServer class

    Attributes:
        module: python module providing the service
        name: service name
        api_version: enum class holding the supported/current API versions
        manager: object instance to which delegating services requests
        register_method: GRPC-generated method to be called for service registration on the server instance
        client_stub: GRPC-generated class holding the service client stub
    """

    module: ModuleType
    name: str
    api_version: EnumTypeWrapper
    manager: RpcManager
    register_method: Callable[[object, object], NoReturn]
    client_stub: TypeVar


class RpcServer(InfoServiceServicer, RpcManager):
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
        super().__init__()
        self.__port = port
        self.calls = []

        # Prepare config manager
        # (by the way, add our own static items)
        config_m = ConfigManager(folders, cli_config, (static_items + [RpcStaticConfig]) if static_items is not None else [RpcStaticConfig], user_items)

        # Create server instance, disabling port reuse
        self.__server = server(futures.ThreadPoolExecutor(max_workers=RpcStaticConfig.MAX_WORKERS.int_val), options=[("grpc.so_reuseport", 0)])

        # Systematically add services:
        # - to be able to answer to "get info" rpc
        # - to handle remote user configuration update
        # - to handle remote loggers configuration update
        self.descriptors = [
            RpcServiceDescriptor(grpc_helper, "info", InfoApiVersion, self, add_InfoServiceServicer_to_server, InfoServiceStub),
            RpcServiceDescriptor(grpc_helper, "config", ConfigApiVersion, config_m, add_ConfigServiceServicer_to_server, ConfigServiceStub),
            RpcServiceDescriptor(grpc_helper, "log", LoggerApiVersion, LogsManager(), add_LoggerServiceServicer_to_server, LoggerServiceStub),
        ]

        # Get servicers
        self.descriptors.extend(descriptors)

        # Prepare auto clients stubs map
        stubs_map = {}

        # Register everything
        self.__info = []
        for descriptor in self.descriptors:
            # Prepare folders and logger
            descriptor.manager.init_folders_n_logger(folders)

            # Build info for service
            versions = list(descriptor.api_version.values())
            versions.remove(0)
            current_version = max(versions)
            info = ServiceInfo(
                name=f"{descriptor.module.__title__}.{descriptor.name}",
                version=descriptor.module.__version__,
                current_api_version=current_version,
                supported_api_version=min(versions),
            )
            self.logger.debug(f"Registering service in RPC server: {descriptor.manager.__class__.__name__} -- {trace_buffer(info)}")

            # Remember info
            self.__info.append(info)

            # Register servicer in RPC server
            descriptor.register_method(RpcServicer(descriptor.manager, info, self), self.__server)

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
        self.logger.debug(f"RPC server started on port {self.__port}")

        # Hook debug signal
        signal(SIGUSR2, self.__dump_debug)

        # Load all managers (including client pointing to all served services)
        to_raise = None
        for descriptor in filter(lambda d: hasattr(d.manager, "preload"), self.descriptors):
            client = RpcClient("localhost", self.__port, stubs_map, name=f"{descriptor.name}-client", logger=descriptor.manager.logger)
            try:
                descriptor.manager.preload(client, folders)
            except Exception as e:
                self.logger.error(f"Error occurred during {descriptor.name} manager loading: {e}\n" + "".join(traceback.format_tb(e.__traceback__)))
                to_raise = e
                break

        # If something bad happened during managers loading, shutdown and raise exception
        if to_raise is not None:
            self.shutdown()
            raise to_raise

    def __dump_debug(self, signum, frame):
        """
        Dumps current threads + pending RPC requests
        """

        # Prepare debug output
        output = Path("/tmp") / f"RpcServerDump-{time.strftime('%Y%m%d%H%M%S')}.txt"
        with output.open("w") as f:
            # Dump threads
            faulthandler.dump_traceback(f, all_threads=True)

            # Dump pending calls
            f.write("\n\nPending RPC calls:\n")
            for call in list(self.calls):
                f.write(f"{call}\n")

    def shutdown(self):
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
            for descriptor in filter(lambda d: not isinstance(d.manager, RpcServer), self.descriptors):
                descriptor.manager.shutdown()

            # Wait for all pending requests to be terminated
            self.logger.debug("Waiting to terminate all requests")
            terminating_server.wait_for_termination()
            self.logger.debug(f"RPC server shut down on port {self.__port}")

            # Removing all rotating loggers
            for descriptor in self.descriptors:
                self._clean_rotating_handler(descriptor.manager.logger)

            # Remove rotating handler for current + root loggers
            self._clean_rotating_handler(logging.getLogger())

    def get(self, request: Empty) -> MultiServiceInfo:
        # Just build message from stored info
        return MultiServiceInfo(items=self.__info)

    def init_folders_n_logger(self, folders: Folders):
        # Override to process root logger as well
        super().init_folders_n_logger(folders)
        self._add_rotating_handler(logging.getLogger())
