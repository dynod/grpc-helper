from pathlib import Path

from pytest_multilog import TestHelper

from grpc_helper import Folders


class TestFolders(TestHelper):
    def test_empty(self):
        # Verify None values are supported
        f = Folders()
        assert f.system is None
        assert f.user is None
        assert f.workspace is None

    def test_valid(self):
        # Verify folders are created
        f = Folders(Path("/tmp"), self.test_folder / "user", self.test_folder / "workspace")
        assert f.system.name == "tmp"
        assert f.user.is_dir()
        assert f.workspace.is_dir()
