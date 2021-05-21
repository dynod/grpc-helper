import copy
import time
import traceback
from logging import getLogger
from threading import current_thread

from grpc_helper.api import Result, ResultCode, ResultStatus, ServiceInfo
from grpc_helper.client import RpcClient
from grpc_helper.errors import RpcException
from grpc_helper.manager import RpcManager
from grpc_helper.meta import RpcMetadata
from grpc_helper.static_config import RpcStaticConfig
from grpc_helper.utils import trace_rpc


class RpcServerMethod:
    def __init__(self, name: str, manager: RpcManager, stub: object, return_type: object, info: ServiceInfo, server: object):
        self.manager_method = getattr(manager, name) if manager is not None else None
        self.name = name
        self.logger = manager.logger if manager is not None else getLogger("RpcServer")
        self.return_type = return_type
        self.info = info
        self.server = server
        self.stub = stub

    def prelude(self, request, context) -> str:
        # Remember call for debug dump
        input_trace = trace_rpc(True, request, context=context)
        logged_call = f"Thread 0x{current_thread().ident:016x} -- " + input_trace
        with self.server.lock:
            self.server.calls.append(logged_call)
        self.logger.debug(input_trace)
        return logged_call

    def epilog(self, logged_call):
        # Forget call from debug dump
        with self.server.lock:
            self.server.calls.remove(logged_call)

    def report_exception(self, context, e: Exception):
        # Something happened during the RPC execution
        stack = "".join(traceback.format_tb(e.__traceback__))
        self.logger.error(f"Exception occurred: {e}\n{stack}")

        # Extract RC if this was a known error
        rc = e.rc if isinstance(e, RpcException) else ResultCode.ERROR

        # Build result according to return type
        r = Result(code=rc, msg=str(e), stack=stack)
        return ResultStatus(r=r) if self.return_type is None else self.return_type(r=r)

    def delegate_call(self, request, context):
        # Verify API version
        metadata = RpcMetadata.from_context(context)
        client_version = None
        if len(metadata.api_version):
            client_version = int(metadata.api_version)
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

            # Reuse metadata from context
            client_meta = copy.copy(metadata)
            client_meta.client = f"{client_meta.client}(proxied)"

            # Temporary client to proxied server (that won't raise exceptions: let's simply forward the output message to final client)
            client = RpcClient(
                self.info.proxy_host if len(self.info.proxy_host) else RpcStaticConfig.MAIN_HOST.str_val,
                self.info.proxy_port,
                {"stub": (self.stub, client_version if client_version is not None else self.info.current_api_version)},
                name=client_meta,
                timeout=RpcStaticConfig.CLIENT_TIMEOUT.float_val,
                logger=self.logger,
                exception=False,
            )

            # Call remote method
            result_provider = getattr(client.stub, self.name)(request)
        else:
            # Not a proxy method, delegate to manager
            result_provider = self.manager_method(request)

        return result_provider


class RpcSimpleMethod(RpcServerMethod):
    def __call__(self, request, context):
        # Call prelude
        logged_call = self.prelude(request, context)

        try:
            # Delegate call (simple output)
            result = self.delegate_call(request, context)
        except Exception as e:
            # Handle exception
            result = self.report_exception(context, e)

        # Call epilog
        self.epilog(logged_call)
        self.logger.debug(trace_rpc(False, result, context=context))

        return result


class RpcStreamingMethod(RpcServerMethod):
    def __call__(self, request, context):
        # Call prelude
        logged_call = self.prelude(request, context)

        try:
            # Delegate call (streaming output)
            result_provider = self.delegate_call(request, context)
            for result in result_provider:
                self.logger.debug(trace_rpc(False, result, context=context))
                yield result
        except Exception as e:
            # Handle exception
            result = self.report_exception(context, e)
            self.logger.debug(trace_rpc(False, result, context=context))
            yield result

        # Call epilog
        self.epilog(logged_call)
