from pathlib import Path

import pytest
from pytest_multilog import TestHelper

from grpc_helper import Folders, RpcServer


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
            self.rpc_port,
            self.sample_register,
            user_items=self.user_items,
            folders=self.folders if with_workspace else Folders(),
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
    def cli_config_items(self) -> dict:
        return {}
