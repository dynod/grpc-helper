from typing import List

from grpc_helper.api import ConfigStatus, ConfigUpdate, Filter, ResultCode
from grpc_helper.api.config_pb2_grpc import ConfigServiceServicer
from grpc_helper.config.cfg_item import ConfigInnerItem
from grpc_helper.errors import RpcException
from grpc_helper.manager import RpcManager


class ConfigManager(ConfigServiceServicer, RpcManager):
    """
    Configuration manager, holding public/private config items, and implementing the ConfigService API
    """

    def __init__(self, public_items: List[ConfigInnerItem], private_items: List[ConfigInnerItem]):
        RpcManager.__init__(self)
        self.public_items = {n: i for n, i in map(lambda i: (i.name, i), public_items)}
        self.private_items = {n: i for n, i in map(lambda i: (i.name, i), private_items)}

    def check_items(self, input_names: list, empty_ok: bool):
        # Check for empty list
        if not empty_ok and len(input_names) == 0:
            raise RpcException("Input request list is empty", ResultCode.ERROR_PARAM_MISSING)

        # Check for empty name in list
        if any(n == "" for n in input_names):
            raise RpcException("At least one empty name found in input request", ResultCode.ERROR_PARAM_MISSING)

        # Check filter for unknown items
        unknown_items = list(filter(lambda n: n not in self.public_items.keys(), input_names))
        if len(unknown_items):
            raise RpcException("Unknown config item names in filter request: " + ", ".join(unknown_items), ResultCode.ERROR_ITEM_UNKNOWN)

    def build_status(self, names: List[str]) -> ConfigStatus:
        # Build filtered output status
        return ConfigStatus(items=list(map(lambda n: self.public_items[n].item, names)))

    def get(self, request: Filter) -> ConfigStatus:
        """
        Get configuration items, according to input filter
        """

        # Basic checks
        self.check_items(request.names, True)

        with self.lock:
            # Return filtered list
            return self.build_status(request.names)

    def reset(self, request: Filter) -> ConfigStatus:
        """
        Reset configuration items, according to input filter
        """

        # Basic checks
        self.check_items(request.names, False)

        with self.lock:
            # Reset all request items to their default values
            for name in request.names:
                self.public_items[name].reset()

            # Return filtered list
            return self.build_status(request.names)

    def set(self, request: ConfigUpdate) -> ConfigStatus:  # NOQA:A003
        """
        Update configuration items, according to input request
        """

        # Basic checks
        req_map = {r.name: r.value for r in request}
        self.check_items(req_map.keys(), False)

        # Validate items new values
        for name, value in req_map.items():
            item = self.public_items[name]

            # Can be empty?
            if value == "":
                if not item.can_be_empty:
                    raise RpcException(f"Empty value provided for item {name} (not supported)")
            else:
                # Non-empty value validation
                item.validate(value)
