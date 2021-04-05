import re
import sys
from argparse import Action, ArgumentParser, ArgumentTypeError, Namespace
from os.path import expanduser
from pathlib import Path

from argcomplete.completers import DirectoriesCompleter

from grpc_helper.folders import Folders

# Config item parameter syntax
CONFIG_ITEM_DEF_PATTERN = re.compile("([a-z][a-z0-9-]*)=(.*)")


def expanded_path(arg: str) -> Path:
    # Path with user expanded value
    return Path(expanduser(arg))


class ConfigDictAction(Action):
    """
    Custom action to store config items as a dict
    """

    def __call__(self, parser, namespace, values, option_string=None):
        # First, validate name=value syntax
        m = CONFIG_ITEM_DEF_PATTERN.match(values)
        if m is None:
            raise ArgumentTypeError(f"Invalid syntax for config item definition: {values}")
        name = m.group(1)
        value = m.group(2)

        # Get map (initialized by default arg)
        config_map = getattr(namespace, self.dest)

        # Store value in map
        config_map[name] = value


class RpcCliParser:
    """
    CLI arguments parser for RPC servers

    Constructor arguments:
        description:
            Description string to be displayed in help
        version:
            Version string to be displayed with -V option
    """

    def __init__(self, description: str, version: str = None):
        # Initialize parser
        self.__parser = ArgumentParser(description=description)

        # Add version command
        if version is not None:
            self.parser.add_argument("-V", "--version", action="version", version=version)

    @property
    def parser(self) -> ArgumentParser:
        return self.__parser

    def with_rpc_args(
        self, default_port: int = 54321, default_sys: str = "/etc/grpc_helper", default_usr: str = "~/.config/grpc_helper", default_wks: str = "./grpc_helper"
    ):
        # Folders
        self.parser.add_argument(
            "--system", type=expanded_path, action="store", default=expanded_path(default_sys), help=f"Override system folder (default: {default_sys})"
        ).completer = DirectoriesCompleter
        self.parser.add_argument(
            "--user", type=expanded_path, action="store", default=expanded_path(default_usr), help=f"Override user folder (default: {default_usr})"
        ).completer = DirectoriesCompleter
        self.parser.add_argument(
            "-w", "--workspace", type=expanded_path, action="store", default=expanded_path(default_wks), help=f"Workspace folder (default: {default_wks})"
        ).completer = DirectoriesCompleter

        # RPC server serving port
        self.parser.add_argument("-p", "--port", type=int, action="store", default=default_port, help=f"RPC server port (default: {default_port})")

        # Default config values
        self.parser.add_argument("-c", "--config", metavar="NAME=VALUE", action=ConfigDictAction, default={}, help="Override default configuration item value")

        return self

    def parse(self, input_args: list = None) -> Namespace:
        # By default, parse from CLI args
        input_args = input_args if input_args is not None else sys.argv[1:]

        # Parse!
        args = self.parser.parse_args(input_args)

        # Add folders definition
        setattr(args, "folders", Folders(args.system, args.user, args.workspace))  # NOQA: B010

        return args
