import logging
import os
import signal
import time
from pathlib import Path
from threading import Thread
from typing import List

import pytest
from pytest_multilog import TestHelper

import grpc_helper
from grpc_helper import RpcClient, RpcException, RpcManager, RpcServer, RpcServiceDescriptor
from grpc_helper.api import Empty, InfoApiVersion, Result, ResultCode
from tests.api import SampleApiVersion
from tests.api.sample_pb2_grpc import SampleServiceServicer, SampleServiceStub, add_SampleServiceServicer_to_server


class SampleServicer(SampleServiceServicer, RpcManager):
    def __init__(self):
        super().__init__()
        self.wait_a_bit = False

    def method1(self, request: Empty) -> Result:
        self.logger.info("In SampleServicer.method1!!!")

        # Sleep if requested
        if self.wait_a_bit:
            time.sleep(3)

        # Use auto-client to access other services
        s = self.client.info.get(Empty())

        return Result(msg=f"Found info count: {len(s.items)}")

    def method2(self, request: Empty) -> Result:
        self.logger.info("Raising error!!!")
        raise RpcException("sample error", rc=12)


class TestRpcServer(TestHelper):
    @property
    def sample_register(self) -> list:
        self.servicer = SampleServicer()
        return [RpcServiceDescriptor(grpc_helper, "sample", SampleApiVersion, self.servicer, add_SampleServiceServicer_to_server, SampleServiceStub)]

    @pytest.fixture
    def sample_server(self):
        # Start server
        srv = RpcServer(self.rpc_port, self.sample_register)

        # Yield to test
        yield srv

        # Shutdown server
        srv.shutdown()

    @pytest.fixture
    def client(self, sample_server):
        # Use server auto-client
        yield sample_server.auto_client

    @property
    def rpc_port(self) -> int:
        return 52100 + self.worker_index

    def test_server(self, client):
        # Normal call
        s = client.sample.method1(Empty())
        assert s.code == ResultCode.OK
        assert s.msg == "Found info count: 2"

    def test_debug_dump(self, client):
        # Tweak servicer to send debug signal to serving process
        self.servicer.wait_a_bit = True

        # Clean test files
        def dump_files() -> List[Path]:
            return list(Path("/tmp").glob("RpcServerDump-*.txt"))

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
            assert s.code == ResultCode.OK

        t = Thread(target=call_method1)
        t.start()
        time.sleep(0.5)

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
            RpcServer(self.rpc_port, self.sample_register)
            raise AssertionError("Shouldn't get here")
        except RpcException as e:
            assert e.rc == ResultCode.ERROR_PORT_BUSY

    def test_get_info(self, client):
        # Try a "get info" call
        s = client.info.get(Empty())
        assert len(s.items) == 2
        info = s.items[0]
        assert info.name == "grpc-helper.info"
        assert info.version == grpc_helper.__version__
        assert info.current_api_version == InfoApiVersion.INFO_API_CURRENT
        assert info.supported_api_version == InfoApiVersion.INFO_API_SUPPORTED
        info = s.items[1]
        assert info.name == "grpc-helper.sample"
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
