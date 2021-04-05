import pytest
from pytest_multilog import TestHelper

from grpc_helper import Folders, RpcServer


class TestUtils(TestHelper):
    @property
    def rpc_port(self) -> int:
        return 52100 + self.worker_index

    def new_server_instance(self, workspace: str = "wks"):
        # Create new server instance
        self.server = RpcServer(
            self.rpc_port,
            self.sample_register,
            user_items=self.user_items,
            folders=Folders(workspace=(self.test_folder / workspace) if workspace is not None else None),
        )

    @pytest.fixture
    def sample_server(self):
        # Start server
        self.new_server_instance()

        # Yield to test
        yield self.server

        # Shutdown server
        self.server.shutdown()

    @pytest.fixture
    def client(self, sample_server):
        # Use server auto-client
        yield sample_server.auto_client

    @property
    def sample_register(self) -> list:
        return []

    @property
    def user_items(self) -> list:
        return None

    @property
    def cli_config_items(self) -> dict:
        return {}
