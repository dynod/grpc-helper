import os
from argparse import ArgumentTypeError
from pathlib import Path

from pytest_multilog import TestHelper

from grpc_helper import RpcCliParser
from grpc_helper.utils import is_windows


class TestCli(TestHelper):
    def test_default_params(self):
        # Parser with default parameters
        args = RpcCliParser("Some description", version="1.0.0").with_rpc_args().parse([])
        assert args.folders.system == Path("C:\\grpc_helper" if is_windows() else "/etc/grpc_helper")
        assert args.folders.user == Path(os.environ["HOME"]) / ".config" / "grpc_helper"
        assert args.folders.workspace == Path("grpc_helper")
        assert args.port == 54321
        assert len(args.config) == 0

    def test_invalid_config(self):
        # Test parser
        p = RpcCliParser("Some description").with_rpc_args()

        try:
            # Verify with bad config syntax
            p.parse(["-c", "invalid syntax"])
        except ArgumentTypeError as e:
            assert str(e) == "Invalid syntax for config item definition: invalid syntax"
        else:
            raise AssertionError("Shouldn't get here")

        try:
            # Verify with bad config item name
            p.parse(["-c", "Invalid@Name=foo"])
        except ArgumentTypeError as e:
            assert str(e) == "Invalid syntax for config item definition: Invalid@Name=foo"
        else:
            raise AssertionError("Shouldn't get here")

    def test_config(self):
        # Test parser
        p = RpcCliParser("Some description").with_rpc_args()

        args = p.parse(["-c", "foo=bar", "--config", "some-other=123"])
        assert len(args.config) == 2
        assert args.config["foo"] == "bar"
        assert args.config["some-other"] == "123"
