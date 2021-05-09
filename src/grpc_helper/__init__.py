from configparser import ConfigParser
from pathlib import Path

from pkg_resources import DistributionNotFound, get_distribution

__title__ = "grpc-helper"
try:
    __version__ = get_distribution(__title__).version
except DistributionNotFound:  # pragma: no cover
    # For debug
    with (Path(__file__).parent.parent.parent.parent / "setup.cfg").open("r") as f:
        c = ConfigParser()
        c.read_file(f.readlines())
        __version__ = c.get("metadata", "version")

# Public RPC API
from grpc_helper.cli import RpcCliParser
from grpc_helper.client import RpcClient
from grpc_helper.errors import RpcException
from grpc_helper.folders import Folders
from grpc_helper.manager import RpcManager, RpcProxiedManager
from grpc_helper.server import RpcServer, RpcServiceDescriptor
from grpc_helper.static_config import RpcStaticConfig

__all__ = ["RpcServer", "RpcServiceDescriptor", "RpcClient", "RpcException", "RpcManager", "RpcProxiedManager", "Folders", "RpcCliParser", "RpcStaticConfig"]
