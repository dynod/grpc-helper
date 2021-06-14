import copy
import os
import socket
import time
from logging import Logger, getLogger
from typing import TypeVar, Union

from grpc import RpcError, StatusCode, insecure_channel

from grpc_helper.api import Result, ResultCode
from grpc_helper.errors import RpcException
from grpc_helper.meta import RpcMetadata
from grpc_helper.utils import get_current_ip, is_streaming, trace_rpc


class RetryMethod:
    def __init__(self, name: str, stub, channel: str, timeout: float, metadata: RpcMetadata, logger: Logger, exception: bool, custom_exception: TypeVar):
        self.m_name = name
        self.s_name = f"{stub.__class__.__name__}({channel})"
        self.stub = stub
        self.timeout = timeout
        self.metadata = metadata
        self.logger = logger
        self.exception = exception
        self.custom_exception = custom_exception if custom_exception is not None else RpcException

    def prelude(self, request):
        trace = trace_rpc(True, request, context=self.metadata, method=f"{self.s_name}.{self.m_name}")
        self.logger.debug(trace)
        return (trace, time.time())

    def handle_exception(self, trace: str, first_try, e: RpcError):
        if e.code() == StatusCode.UNAVAILABLE and self.timeout is not None and (time.time() - first_try) < self.timeout:
            # Server is not available, and timeout didn't expired yet: sleep and retry
            time.sleep(0.5)
            self.logger.debug(f"<RPC> >> {self.s_name}.{self.m_name}... (retry because of 'unavailable' error; details: '{e.details()}')")
        else:
            # Timed out or any other reason: raise exception
            self.logger.debug(f"<RPC> >> {self.s_name}.{self.m_name} error: {str(e)}")
            raise RpcException(f"RPC error (on {trace}): {e}", rc=ResultCode.ERROR_RPC)

    def raise_result(self, result, trace):
        if (
            (hasattr(result, "r") and isinstance(result.r, Result))
            and result.r.code > ResultCode.OK
            and result.r.code < ResultCode.ERROR_CUSTOM
            and self.exception
        ):
            # Error occurred
            raise self.custom_exception(f"RPC returned error: {trace}", rc=result.r.code)


class RetryStreamingMethod(RetryMethod):
    def __call__(self, request):
        # Call prelude
        trace, first_try = self.prelude(request)

        # Loop to handle retries
        while True:
            try:
                # Call real stub method, with metadata
                for result in getattr(self.stub, self.m_name)(request, metadata=self.metadata.as_tuple()):
                    out_trace = trace_rpc(False, result, context=self.metadata, method=f"{self.s_name}.{self.m_name}")
                    self.logger.debug(out_trace)
                    self.raise_result(result, out_trace)
                    yield result
                break
            except RpcError as e:
                self.handle_exception(trace, first_try, e)


class RetrySimpleMethod(RetryMethod):
    def __call__(self, request):
        # Call prelude
        trace, first_try = self.prelude(request)

        # Loop to handle retries
        while True:
            try:
                # Call real stub method, with metadata
                result = getattr(self.stub, self.m_name)(request, metadata=self.metadata.as_tuple())
                out_trace = trace_rpc(False, result, context=self.metadata, method=f"{self.s_name}.{self.m_name}")
                self.logger.debug(out_trace)

                # May raise an exception...
                self.raise_result(result, out_trace)
                return result
            except RpcError as e:
                self.handle_exception(trace, first_try, e)


# Utility class to handle stub retry
# i.e. permissive stub that allows server to be temporarily unavailable
class RetryStub:
    def __init__(self, real_stub, channel: str, timeout: float, metadata: RpcMetadata, logger: Logger, exception: bool, custom_exception: TypeVar):
        # Fake the stub methods
        for n in filter(lambda x: not x.startswith("__") and callable(getattr(real_stub, x)), dir(real_stub)):
            setattr(
                self,
                n,
                RetryStreamingMethod(n, real_stub, channel, timeout, metadata, logger, exception, custom_exception)
                if is_streaming(real_stub, n)
                else RetrySimpleMethod(n, real_stub, channel, timeout, metadata, logger, exception, custom_exception),
            )


class RpcClient:
    """
    Wrapper to GRPC api to setup an RPC client.

    Arguments:
        host:
            host string for RPC server to connect to.
        port:
            TCP port for RPC server to connect to.
        stubs_map:
            name:(stub,api_version) item maps, used to instantiate and attach (generated) stubs to current client instance
            e.g. if a {"foo": (FooServiceStub,1)} map is provided, methods of the Foo service can be accessed through a client.foo.xxx call
        timeout:
            timeout for unreachable server (in seconds; default: 60)
        name:
            can be either:
                - a string: client name, which will appear in server logs; RPC metadata will be generated from this name + current host
                - an RpcMetadata instance (when proxying calls from another client)
        logger:
            Logger instance to be used when logging calls
        exception:
            Raise RpcException if RPC includes a non-OK ResultCode status
        custom_exception:
            Use this provided custom class instead of RpcException when raising non-OK ResultCode exceptions
    """

    def __init__(
        self,
        host: str,
        port: int,
        stubs_map: dict,
        timeout: float = 60,
        name: Union[str, RpcMetadata] = "",
        logger: Logger = None,
        exception: bool = True,
        custom_exception: TypeVar = None,
    ):
        # Prepare metadata for RPC calls
        shared_metadata = name if isinstance(name, RpcMetadata) else RpcMetadata(name, self.get_user(), socket.gethostname(), get_current_ip())

        # Prepare logger
        self.logger = logger if logger is not None else getLogger("RpcClient")

        # Create channel
        self.target_host = f"{host}:{port}"
        self.logger.debug(f"Initializing RPC client for {self.target_host}")
        channel = insecure_channel(self.target_host)

        # Handle stubs hooking
        for name, typ_n_ver in stubs_map.items():
            typ, ver = typ_n_ver
            metadata = copy.copy(shared_metadata)
            if ver is not None:
                metadata.api_version = str(ver)
            self.logger.debug(f" >> adding {name} stub to client (api version: {ver})")
            setattr(self, name, RetryStub(typ(channel), self.target_host, timeout, metadata, self.logger, exception, custom_exception))

        self.logger.debug(f"RPC client ready for {self.target_host}")

    def get_user(self):
        if os.name == "posix":
            # Resolve user
            uid = os.getuid()
            try:
                # Try from pwd
                import pwd

                user = pwd.getpwuid(uid).pw_name
            except Exception:  # pragma: no cover
                # Not in pwd database, just keep UID
                user = f"{uid}"
            return user

        # Otherwise, just get login
        return os.getlogin()  # pragma: no cover
