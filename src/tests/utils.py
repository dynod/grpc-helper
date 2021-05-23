import os
from pathlib import Path
from typing import List

import pytest
from pytest_multilog import TestHelper

import grpc_helper
from grpc_helper import Folders, RpcProxiedManager, RpcServer, RpcServiceDescriptor, RpcStaticConfig
from tests.api import SampleApiVersion
from tests.api.sample_pb2_grpc import (
    AnotherSampleServiceServicer,
    AnotherSampleServiceStub,
    SampleServiceServicer,
    SampleServiceStub,
    add_AnotherSampleServiceServicer_to_server,
    add_SampleServiceServicer_to_server,
)


class TestUtils(TestHelper):
    @property
    def rpc_port(self) -> int:
        return 52100 + self.worker_index

    @property
    def workspace_path(self) -> Path:
        return self.test_folder / "wks"

    @property
    def folders(self) -> Folders:
        return Folders(workspace=(self.workspace_path))

    def new_server_instance(self, with_workspace: bool = True):
        # Create new server instance
        self.server = RpcServer(
            self.rpc_port, self.sample_register, user_items=self.user_items, folders=self.folders if with_workspace else Folders(), with_events=True
        )

    def shutdown_server_instance(self):
        # Shutdown server
        self.server.shutdown()

    @pytest.fixture
    def sample_server(self):
        # Start server
        self.new_server_instance()

        # Yield to test
        yield self.server

        # Shutdown server
        self.shutdown_server_instance()

    @pytest.fixture
    def client(self, sample_server):
        # Use server auto-client
        yield sample_server.client

    @property
    def sample_register(self) -> list:
        return []

    @property
    def user_items(self) -> list:
        return None

    @property
    def user_items2(self) -> list:
        return None

    @property
    def cli_config_items(self) -> dict:
        return {}

    @property
    def proxy_port(self) -> int:
        return self.rpc_port + 50

    def new_proxy_server(self):
        self.proxy_server = RpcServer(
            self.proxy_port,
            [
                # Proxy service definition for sample services
                RpcServiceDescriptor(
                    grpc_helper, "sample", SampleApiVersion, SampleServiceServicer(), add_SampleServiceServicer_to_server, SampleServiceStub, True
                ),
                RpcServiceDescriptor(
                    grpc_helper,
                    "sample2",
                    SampleApiVersion,
                    AnotherSampleServiceServicer(),
                    add_AnotherSampleServiceServicer_to_server,
                    AnotherSampleServiceStub,
                    True,
                ),
            ],
            folders=Folders(workspace=self.proxy_workspace),
            with_events=True,
        )
        return self.proxy_server

    @property
    def proxy_workspace(self):
        return self.test_folder / "wks_proxy"

    @pytest.fixture
    def proxy_server(self):
        # Short timeout for unregistered proxy
        os.environ["RPC_CLIENT_TIMEOUT"] = "2"

        # Prepare proxy
        self.new_proxy_server()

        # back to test
        yield self.proxy_server

        # Shutdown server
        self.proxy_server.shutdown()
        del os.environ["RPC_CLIENT_TIMEOUT"]

    @property
    def rpc_another_port(self) -> int:
        return self.rpc_port + 100

    def start_another_server(self, use_ip: bool):
        return RpcServer(
            self.rpc_another_port,
            [
                RpcServiceDescriptor(
                    grpc_helper,
                    "sample2",
                    SampleApiVersion,
                    AnotherSampleServicer2() if use_ip else AnotherSampleServicer(),
                    add_AnotherSampleServiceServicer_to_server,
                    AnotherSampleServiceStub,
                )
            ],
            user_items=self.user_items2,
            folders=Folders(workspace=self.test_folder / "wks_another"),
            cli_config={RpcStaticConfig.MAIN_PORT.name: str(self.proxy_port)},
        )

    @pytest.fixture
    def another_server(self, proxy_server):
        # Start server
        another = self.start_another_server(False)

        # Yield to test
        yield another

        # Shutdown server
        another.shutdown()

    @pytest.fixture
    def another_server2(self, proxy_server):
        # Start server
        another = self.start_another_server(True)

        # Yield to test
        yield another

        # Shutdown server
        another.shutdown()


class AnotherSampleServicer(AnotherSampleServiceServicer, RpcProxiedManager):
    def _proxied_services(self) -> List[str]:
        return ["sample2"]

    def _proxied_version(self) -> str:
        return "123"


class AnotherSampleServicer2(AnotherSampleServiceServicer, RpcProxiedManager):
    def _proxied_services(self) -> List[str]:
        return ["sample2"]

    def _proxied_version(self) -> str:
        return "123"

    def _proxy_use_current_host(self) -> bool:
        return True
