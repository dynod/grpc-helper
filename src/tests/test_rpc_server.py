import logging

import pytest
from pytest_multilog import TestHelper

import grpc_helper
from grpc_helper import RpcClient, RpcException, RpcServer, RpcServiceDescriptor
from grpc_helper.api import Empty, InfoApiVersion, Result, ResultCode
from tests.api import SampleApiVersion
from tests.api.sample_pb2_grpc import SampleServiceServicer, SampleServiceStub, add_SampleServiceServicer_to_server


class SampleServicer(SampleServiceServicer):
    def method1(self, request: Empty) -> Result:
        logging.info("In SampleServicer.method1!!!")
        return Result()

    def method2(self, request: Empty) -> Result:
        logging.info("Raising error!!!")
        raise RpcException("sample error", rc=12)


class TestRpcServer(TestHelper):
    @property
    def sample_register(self) -> list:
        return [RpcServiceDescriptor(grpc_helper, SampleApiVersion, SampleServicer(), add_SampleServiceServicer_to_server)]

    @pytest.fixture
    def sample_server(self):
        # Start server
        srv = RpcServer(self.rpc_port, self.sample_register)

        # Yield to test
        yield

        # Shutdown server
        srv.shutdown()

    @pytest.fixture
    def client(self, sample_server):
        # Setup RPC client
        yield RpcClient("127.0.0.1", self.rpc_port, {"sample": (SampleServiceStub, SampleApiVersion.SAMPLE_API_CURRENT)}, name="pytest")

    @property
    def rpc_port(self) -> int:
        return 52100 + self.worker_index

    def test_server(self, client):
        # Normal call
        s = client.sample.method1(Empty())
        assert s.code == ResultCode.OK

    def test_exceptions(self, client):
        # Error call
        try:
            client.sample.method2(Empty())
            raise AssertionError("Shouldn't get here")
        except RpcException as e:
            assert e.rc == 12

    def test_server_forbidden_port(self):
        # Try to use a system port
        try:
            RpcServer(22, self.sample_register)
            raise AssertionError("Shouldn't get here")
        except RpcException as e:
            assert e.rc == ResultCode.ERROR_PORT_BUSY

    def test_get_info(self, client):
        # Try a "get info" call
        s = client.info.get(Empty())
        assert len(s.items) == 2
        info = s.items[0]
        assert info.name == grpc_helper.__title__
        assert info.version == grpc_helper.__version__
        assert info.current_api_version == InfoApiVersion.INFO_API_CURRENT
        assert info.supported_api_version == InfoApiVersion.INFO_API_SUPPORTED
        info = s.items[1]
        assert info.name == grpc_helper.__title__
        assert info.version == grpc_helper.__version__
        assert info.current_api_version == SampleApiVersion.SAMPLE_API_CURRENT
        assert info.supported_api_version == SampleApiVersion.SAMPLE_API_SUPPORTED

    def test_client_no_version(self, sample_server):
        # Try with client not providing API version
        c = RpcClient("127.0.0.1", self.rpc_port, {"sample": (SampleServiceStub, None)})
        s = c.sample.method1(Empty())
        assert s.code == ResultCode.OK

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
        self.check_logs("(retry)")

    def test_not_implemented(self, client):
        # Not implemented call
        try:
            client.sample.method3(Empty())
            raise AssertionError("Shouldn't get there")
        except RpcException as e:
            assert e.rc == ResultCode.ERROR_RPC
