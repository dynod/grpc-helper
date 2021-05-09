import json
import logging
import os
import signal
import time
from pathlib import Path
from threading import Event, Thread
from typing import Iterable, List

import grpc_helper
from grpc_helper import Folders, RpcClient, RpcException, RpcManager, RpcServer, RpcServiceDescriptor
from grpc_helper.api import (
    ConfigApiVersion,
    Empty,
    Filter,
    LoggerApiVersion,
    ProxyRegisterRequest,
    Result,
    ResultCode,
    ResultStatus,
    ServerApiVersion,
    ShutdownRequest,
)
from tests.api import SampleApiVersion, SampleRequest, SampleResponse
from tests.api.sample_pb2_grpc import SampleServiceServicer, SampleServiceStub, add_SampleServiceServicer_to_server
from tests.utils import TestUtils


class SampleServicer(SampleServiceServicer, RpcManager):
    def __init__(self):
        super().__init__()
        self.wait_a_bit = False
        self.is_shutdown = False

    def _shutdown(self):
        self.logger.info("Shutting down service")
        self.is_shutdown = True

    def method1(self, request: Empty) -> ResultStatus:
        self.logger.info("In SampleServicer.method1!!!")

        # Sleep if requested
        if self.wait_a_bit:
            time.sleep(3)

        # Use auto-client to access other services (only if not shutdown in the meantime)
        s = None
        if not self.is_shutdown:
            s = self.client.srv.info(Filter())

        return ResultStatus(r=Result(msg=f"Found info count: {len(s.items) if s is not None else None}"))

    def method2(self, request: Empty) -> ResultStatus:
        self.logger.info("Raising error!!!")
        raise RpcException("sample error", rc=12)

    def method4(self, request: SampleRequest) -> SampleResponse:
        self.logger.info(f"request: {request.foo}")
        return SampleResponse(bar=request.foo)

    def s_method1(self, request: Iterable[SampleRequest]) -> ResultStatus:
        out = ""
        for req in request:
            self.logger.info(f"request: {req.foo}")
            out += req.foo
        return ResultStatus(r=Result(msg=out))

    def s_method2(self, request: SampleRequest) -> ResultStatus:
        for foo in ["abc", "def", "ghi", request.foo]:
            # Raise exception on "error" string
            if foo == "error":
                raise RpcException("Sample error occurred", rc=ResultCode.ERROR_CUSTOM)
            yield ResultStatus(r=Result(msg=foo))


class TestRpcServer(TestUtils):
    @property
    def sample_register(self) -> list:
        self.servicer = SampleServicer()
        return [RpcServiceDescriptor(grpc_helper, "sample", SampleApiVersion, self.servicer, add_SampleServiceServicer_to_server, SampleServiceStub)]

    def test_server(self, client):
        # Normal call
        s = client.sample.method1(Empty())
        assert s.r.code == ResultCode.OK
        assert s.r.msg == "Found info count: 4"

    def test_debug_dump(self, client):
        # Tweak servicer to send debug signal to serving process
        self.servicer.wait_a_bit = True

        # Clean test files
        def dump_files() -> List[Path]:
            return list((self.workspace_path / "logs").glob("RpcServerDump-*.txt"))

        previous_ones = dump_files()
        if len(previous_ones):
            for previous_one in previous_ones:
                logging.warning(f"removing old file: {previous_one}")
                previous_one.unlink()
        previous_ones = dump_files()
        assert len(previous_ones) == 0

        # Normal call in separated thread
        def call_method1():
            s = client.sample.method1(Empty())
            assert s.r.code == ResultCode.OK

        t = Thread(target=call_method1)
        t.start()
        time.sleep(1)

        # Fake a "dump thread" command
        logging.warning(">> Sending signal")
        os.kill(os.getpid(), signal.SIGUSR2)
        logging.warning("<< Sending signal")

        # Make sure we're done
        t.join()

        # Should be one and only one file
        new_ones = dump_files()
        assert len(new_ones) == 1
        with new_ones[0].open("r") as f:
            # Verify method call is present in dump
            assert " >>> SampleService.method1 (Empty{})" in f.read()  # NOQA: P103

    def test_exceptions(self, client):
        # Error call
        try:
            client.sample.method2(Empty())
            raise AssertionError("Shouldn't get here")
        except RpcException as e:
            assert e.rc == 12

    def test_server_busy(self, sample_server):
        try:
            # Try to use the same port again
            RpcServer(self.rpc_port, self.sample_register, folders=Folders(workspace=self.test_folder / "wks2"))
            raise AssertionError("Shouldn't get here")
        except RpcException as e:
            assert e.rc == ResultCode.ERROR_PORT_BUSY

    def test_get_info(self, client):
        # Try a "get info" call
        s = client.srv.info(Filter())
        assert len(s.items) == 4
        info = s.items[0]
        assert info.name == "srv"
        assert info.version == f"grpc-helper:{grpc_helper.__version__}"
        assert info.current_api_version == ServerApiVersion.SERVER_API_CURRENT
        assert info.supported_api_version == ServerApiVersion.SERVER_API_SUPPORTED
        info = s.items[1]
        assert info.name == "config"
        assert info.version == f"grpc-helper:{grpc_helper.__version__}"
        assert info.current_api_version == ConfigApiVersion.CONFIG_API_CURRENT
        assert info.supported_api_version == ConfigApiVersion.CONFIG_API_SUPPORTED
        info = s.items[2]
        assert info.name == "log"
        assert info.version == f"grpc-helper:{grpc_helper.__version__}"
        assert info.current_api_version == LoggerApiVersion.LOGGER_API_CURRENT
        assert info.supported_api_version == LoggerApiVersion.LOGGER_API_SUPPORTED
        info = s.items[3]
        assert info.name == "sample"
        assert info.version == f"grpc-helper:{grpc_helper.__version__}"
        assert info.current_api_version == SampleApiVersion.SAMPLE_API_CURRENT
        assert info.supported_api_version == SampleApiVersion.SAMPLE_API_SUPPORTED

    def test_client_no_version(self, sample_server):
        # Try with client not providing API version
        c = RpcClient("127.0.0.1", self.rpc_port, {"sample": (SampleServiceStub, None)})
        s = c.sample.method1(Empty())
        assert s.r.code == ResultCode.OK

    def test_client_too_old(self, sample_server):
        # Try with client with too old API version
        c = RpcClient("127.0.0.1", self.rpc_port, {"sample": (SampleServiceStub, SampleApiVersion.SAMPLE_API_CURRENT - 1)})
        try:
            c.sample.method1(Empty())
            raise AssertionError("Shouldn't get here")
        except RpcException as e:
            assert e.rc == ResultCode.ERROR_API_CLIENT_TOO_OLD

    def test_server_too_old(self, sample_server):
        # Try with client with API version > server version
        c = RpcClient("127.0.0.1", self.rpc_port, {"sample": (SampleServiceStub, SampleApiVersion.SAMPLE_API_CURRENT + 1)})
        try:
            c.sample.method1(Empty())
            raise AssertionError("Shouldn't get here")
        except RpcException as e:
            assert e.rc == ResultCode.ERROR_API_SERVER_TOO_OLD

    def test_no_server(self):
        # Test behavior when client request is made and server is not ready
        c = RpcClient("127.0.0.1", 1, {"sample": (SampleServiceStub, SampleApiVersion.SAMPLE_API_CURRENT)}, timeout=None)
        try:
            c.sample.method1(Empty())
            raise AssertionError("Shouldn't get there")
        except RpcException as e:
            assert e.rc == ResultCode.ERROR_RPC

    def test_no_server_timeout(self):
        # Same as above, with timeout
        c = RpcClient("127.0.0.1", 1, {"sample": (SampleServiceStub, SampleApiVersion.SAMPLE_API_CURRENT)}, timeout=1)
        try:
            c.sample.method1(Empty())
            raise AssertionError("Shouldn't get there")
        except RpcException as e:
            assert e.rc == ResultCode.ERROR_RPC

        # Verify we retried at least one time
        self.check_logs("(retry because of 'unavailable' error; details: 'failed to connect to all addresses')")

    def test_not_implemented(self, client):
        # Not implemented call
        try:
            client.sample.method3(Empty())
            raise AssertionError("Shouldn't get there")
        except RpcException as e:
            assert e.rc == ResultCode.ERROR_RPC

    def test_graceful_shutdown(self, client):
        # Tweak servicer to wait a bit
        self.servicer.wait_a_bit = True

        # Parallelize shutdown
        def shutdown_server():
            # Wait to be in method
            init = time.time()
            while (time.time() - init) < 5:
                try:
                    self.check_logs("In SampleServicer.method1!!!")
                    break
                except Exception:
                    time.sleep(0.5)

            # Shutdown server
            logging.warning("-- Calling shutdown from debug thread")
            self.shutdown_server_instance()
            logging.warning("-- Shutdown returned in debug thread")

        t = Thread(target=shutdown_server)
        t.start()

        # Parallelize call and shutdown
        s = client.sample.method1(Empty())
        assert s.r.msg == "Found info count: None"

        # Just make sure debug thread is terminated
        t.join()

    def wait_for_shutdown(self, timeout: int):
        assert self.server.is_running
        self.server.wait_shutdown()

    def test_immediate_shutdown_rpc(self, client):
        # Just ask for immediate server shutdown through RPC
        client.srv.shutdown(ShutdownRequest(timeout=-1))

        # Wait for server to be shutdown
        self.wait_for_shutdown(2)

    def test_delayed_shutdown_rpc(self, client):
        # Just ask for delayed server shutdown through RPC
        client.srv.shutdown(ShutdownRequest(timeout=1))

        # Wait for server to be shutdown
        self.wait_for_shutdown(4)

        # Check logs
        self.check_logs("!!! Will shutdown in 1s !!!")

    def test_unregistered_proxy(self, proxy_server: RpcServer):
        # Try a client call
        try:
            proxy_server.client.sample.method4(SampleRequest(foo="test"))
            raise AssertionError("shouldn't get here")
        except RpcException as e:
            assert e.rc == ResultCode.ERROR_PROXY_UNREGISTERED

    def test_late_registered_proxy(self, proxy_server: RpcServer, client):
        sync_event = Event()

        # Prepare thread for late proxy registration
        def register():
            logging.info("About to register")
            sync_event.set()
            time.sleep(1)
            proxy_server.client.srv.proxy_register(ProxyRegisterRequest(names=["sample"], version="123", port=self.rpc_port, host="localhost"))

        t = Thread(target=register)
        t.start()

        # Wait for event
        sync_event.wait()

        # Launch client call
        s = proxy_server.client.sample.method4(SampleRequest(foo="test"))
        assert s.bar == "test"

        # Just clean thread...
        t.join()

        # Make sure we waited at least one 0.5s loop
        self.check_logs("Proxy not registered yet for method method4: wait a bit...")

    def test_proxy_register_missing_params(self, proxy_server):
        # Missing params
        try:
            proxy_server.client.srv.proxy_register(ProxyRegisterRequest())
            raise AssertionError("shouldn't get here")
        except RpcException as e:
            assert e.rc == ResultCode.ERROR_PARAM_MISSING
        try:
            proxy_server.client.srv.proxy_register(ProxyRegisterRequest(names=[""]))
            raise AssertionError("shouldn't get here")
        except RpcException as e:
            assert e.rc == ResultCode.ERROR_PARAM_MISSING
        try:
            proxy_server.client.srv.proxy_register(ProxyRegisterRequest(names=["sample"]))
            raise AssertionError("shouldn't get here")
        except RpcException as e:
            assert e.rc == ResultCode.ERROR_PARAM_MISSING
        try:
            proxy_server.client.srv.proxy_register(ProxyRegisterRequest(names=["sample"], version="1"))
            raise AssertionError("shouldn't get here")
        except RpcException as e:
            assert e.rc == ResultCode.ERROR_PARAM_MISSING
        try:
            proxy_server.client.srv.proxy_register(ProxyRegisterRequest(names=["sample"], port=12))
            raise AssertionError("shouldn't get here")
        except RpcException as e:
            assert e.rc == ResultCode.ERROR_PARAM_MISSING

    def test_proxy_register_unknown_service(self, proxy_server):
        # Unknown service
        try:
            proxy_server.client.srv.proxy_register(ProxyRegisterRequest(names=["foo"]))
            raise AssertionError("shouldn't get here")
        except RpcException as e:
            assert e.rc == ResultCode.ERROR_ITEM_UNKNOWN

    def test_proxy_register_not_a_proxy(self, proxy_server):
        # Service which is not a proxy one
        try:
            proxy_server.client.srv.proxy_register(ProxyRegisterRequest(names=["srv"]))
            raise AssertionError("shouldn't get here")
        except RpcException as e:
            assert e.rc == ResultCode.ERROR_PARAM_INVALID

    def test_proxy_register_n_forget(self, proxy_server, client):
        # First list
        s = proxy_server.client.srv.info(Filter(names=["sample"]))
        info = s.items[0]
        assert info.name == "sample"
        assert info.is_proxy
        assert info.proxy_host == ""
        assert info.proxy_port == 0
        assert info.version == f"grpc-helper:{grpc_helper.__version__}"

        # No persistence
        proxy_config = self.proxy_workspace / "proxy.json"
        assert not proxy_config.exists()

        # Register
        proxy_server.client.srv.proxy_register(ProxyRegisterRequest(names=["sample"], version="123", port=self.rpc_port, host="localhost"))

        # Verify persistence
        assert proxy_config.exists()
        with proxy_config.open("r") as f:
            model = json.load(f)
        assert "sample" in model

        # List again
        s = proxy_server.client.srv.info(Filter(names=["sample"]))
        info = s.items[0]
        assert info.name == "sample"
        assert info.is_proxy
        assert info.proxy_host == "localhost"
        assert info.proxy_port == self.rpc_port
        assert info.version == "123"

        # Try a simple call
        s = proxy_server.client.sample.method1(Empty())
        assert s.r.msg == "Found info count: 4"

        # Shutdown / reload to verify persistence
        proxy_server.shutdown()
        proxy_server = self.new_proxy_server()

        # Try a simple call again
        s = proxy_server.client.sample.method1(Empty())
        assert s.r.msg == "Found info count: 4"

        # Forget
        proxy_server.client.srv.proxy_forget(Filter(names=["sample"]))

        # Verify persistence
        assert proxy_config.exists()
        with proxy_config.open("r") as f:
            model = json.load(f)
        assert len(model) == 0

        # List again
        s = proxy_server.client.srv.info(Filter(names=["sample"]))
        info = s.items[0]
        assert info.name == "sample"
        assert info.is_proxy
        assert info.proxy_host == ""
        assert info.proxy_port == 0
        assert info.version == "123"

    def test_proxy_error_method(self, proxy_server, client):
        # Register proxy
        proxy_server.client.srv.proxy_register(ProxyRegisterRequest(names=["sample"], version="123", port=self.rpc_port))

        # Try method raising an error
        try:
            proxy_server.client.sample.method2(Empty())
            raise AssertionError("shouldn't get here")
        except RpcException as e:
            assert "sample error" in str(e)
            assert e.rc == 12

    def streaming_input(self) -> Iterable[SampleRequest]:
        for foo in ["abc", "def", "ghi"]:
            yield SampleRequest(foo=foo)

    def test_input_stream(self, client):
        # Verify all is OK with input streaming method
        s = client.sample.s_method1(self.streaming_input())
        assert s.r.msg == "abcdefghi"

    def test_output_stream(self, client):
        # Verify all is OK with output streaming method
        results = False
        for s in client.sample.s_method2(SampleRequest()):
            results = True
            assert s.r.msg in ["abc", "def", "ghi", ""]
        assert results

    def test_error_stream(self, client):
        # Verify behavior is ok with server raising an error while streaming
        results = False
        try:
            for s in client.sample.s_method2(SampleRequest(foo="error")):
                results = True
                assert s.r.msg in ["abc", "def", "ghi"]
            raise AssertionError("Shouldn't get here")
        except RpcException as e:
            assert e.rc == ResultCode.ERROR_CUSTOM
        assert results

    def test_not_implemented_stream(self, client):
        # Verify behavior is ok with server raising an RPC error while streaming
        results = False
        try:
            for _s in client.sample.s_method3(self.streaming_input()):
                results = True
            raise AssertionError("Shouldn't get here")
        except RpcException as e:
            assert e.rc == ResultCode.ERROR_RPC
        assert not results

    def test_proxy_input_streaming(self, proxy_server, client):
        # Register proxy
        proxy_server.client.srv.proxy_register(ProxyRegisterRequest(names=["sample"], version="123", port=self.rpc_port))

        # Try streaming method
        s = proxy_server.client.sample.s_method1(self.streaming_input())
        assert s.r.msg == "abcdefghi"

    def test_proxy_output_streaming(self, proxy_server, client):
        # Register proxy
        proxy_server.client.srv.proxy_register(ProxyRegisterRequest(names=["sample"], version="123", port=self.rpc_port))

        # Try streaming method
        results = False
        for s in proxy_server.client.sample.s_method2(SampleRequest()):
            results = True
            assert s.r.msg in ["abc", "def", "ghi", ""]
        assert results

    def test_proxied_servers(self, proxy_server, client, another_server):
        # No proxied servers
        assert len(proxy_server._proxied_servers) == 0

        # Register proxy
        proxy_server.client.srv.proxy_register(ProxyRegisterRequest(names=["sample"], version="123", port=self.rpc_port))

        # One proxied server
        assert len(proxy_server._proxied_servers) == 1
        assert ("localhost", self.rpc_port) in proxy_server._proxied_servers

        # Register another proxy
        proxy_server.client.srv.proxy_register(ProxyRegisterRequest(names=["sample2"], version="123", port=self.rpc_another_port))

        # Two proxied servers
        assert len(proxy_server._proxied_servers) == 2
        assert ("localhost", self.rpc_port) in proxy_server._proxied_servers
        assert ("localhost", self.rpc_another_port) in proxy_server._proxied_servers
