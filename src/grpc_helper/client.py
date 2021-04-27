import logging
import os
import pwd
import socket
import time
from logging import Logger

from grpc import RpcError, StatusCode, insecure_channel

from grpc_helper.api import Result, ResultCode
from grpc_helper.errors import RpcException
from grpc_helper.trace import trace_rpc


# Utility class to handle stub retry
# i.e. permissive stub that allows server to be temporarily unavailable
class RetryStub:
    class RetryMethod:
        def __init__(self, name: str, stub, channel: str, timeout: float, metadata: tuple, logger: Logger, exception: bool):
            self.m_name = name
            self.s_name = f"{stub.__class__.__name__}({channel})"
            self.stub = stub
            self.timeout = timeout
            self.metadata = metadata
            self.logger = logger
            self.exception = exception

        def __call__(self, request):
            trace = trace_rpc(True, request, method=f"{self.s_name}.{self.m_name}")
            self.logger.debug(trace)
            first_try = time.time()

            # Loop to handle retries
            while True:
                try:
                    # Call real stub method, with metadata
                    result = getattr(self.stub, self.m_name)(request, metadata=self.metadata)
                    break
                except RpcError as e:
                    if e.code() == StatusCode.UNAVAILABLE and self.timeout is not None and (time.time() - first_try) < self.timeout:
                        # Server is not available, and timeout didn't expired yet: sleep and retry
                        time.sleep(0.5)
                        self.logger.debug(f"<RPC> >> {self.s_name}.{self.m_name}... (retry because of 'unavailable' error; details: '{e.details()}')")
                    else:
                        # Timed out or any other reason: raise exception
                        self.logger.error(f"<RPC> >> {self.s_name}.{self.m_name} error: {str(e)}")
                        raise RpcException(f"RPC error (on {trace}): {e}", rc=ResultCode.ERROR_RPC)

            trace = trace_rpc(False, result, method=f"{self.s_name}.{self.m_name}")
            self.logger.debug(trace)

            # May raise an exception...
            if (hasattr(result, "r") and isinstance(result.r, Result)) and result.r.code != ResultCode.OK and self.exception:
                # Error occurred
                raise RpcException(f"RPC returned error: {trace}", rc=result.r.code)

            return result

    def __init__(self, real_stub, channel: str, timeout: float, metadata: tuple, logger: Logger, exception: bool):
        # Fake the stub methods
        for n in filter(lambda x: not x.startswith("__") and callable(getattr(real_stub, x)), dir(real_stub)):
            setattr(self, n, RetryStub.RetryMethod(n, real_stub, channel, timeout, metadata, logger, exception))


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
            client name, which will appear in server logs
        logger:
            Logger instance to be used when logging calls
        exception:
            Raise RpcException if RPC includes a non-OK Result status
    """

    def __init__(self, host: str, port: int, stubs_map: dict, timeout: float = 60, name: str = "", logger: Logger = None, exception: bool = True):
        # Prepare metadata for RPC calls
        shared_metadata = [("client", name), ("user", self.get_user()), ("host", socket.gethostname()), ("ip", self.get_ip())]

        # Prepare logger
        self.logger = logger if logger is not None else logging.getLogger("RpcClient")

        # Create channel
        channel_str = f"{host}:{port}"
        self.logger.debug(f"Initializing RPC client for {channel_str}")
        channel = insecure_channel(channel_str)

        # Handle stubs hooking
        for name, typ_n_ver in stubs_map.items():
            typ, ver = typ_n_ver
            metadata = list(shared_metadata)
            if ver is not None:
                metadata.append(("api_version", str(ver)))
            self.logger.debug(f" >> adding {name} stub to client (api version: {ver})")
            setattr(self, name, RetryStub(typ(channel), channel_str, timeout, tuple(metadata), self.logger, exception))

        self.logger.debug(f"RPC client ready for {channel_str}")

    def get_ip(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 1))
            out = s.getsockname()[0]
        except Exception:  # NOQA: B902 # pragma: no cover
            out = ""
        finally:
            s.close()
        return out

    def get_user(self):
        # Resolve user
        uid = os.getuid()
        try:
            # Try from pwd
            user = pwd.getpwuid(uid).pw_name
        except Exception:  # NOQA: B902 # pragma: no cover
            # Not in pwd database, just keep UID
            user = f"{uid}"
        return user
