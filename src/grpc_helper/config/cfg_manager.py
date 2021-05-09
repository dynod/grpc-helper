import os
from pathlib import Path
from typing import Dict, List

from grpc_helper.api import ConfigApiVersion, ConfigItem, ConfigStatus, ConfigUpdate, Filter, ResultCode
from grpc_helper.api.config_pb2_grpc import ConfigServiceServicer, ConfigServiceStub
from grpc_helper.client import RpcClient
from grpc_helper.config.cfg_item import Config
from grpc_helper.errors import RpcException
from grpc_helper.folders import Folders
from grpc_helper.manager import RpcManager

# Config file name
CONFIG_FILE = "config.json"


class ConfigManager(ConfigServiceServicer, RpcManager):
    """
    Configuration manager, holding static/user config items, and implementing the ConfigService API
    """

    def __init__(self, folders: Folders = None, cli_config: Dict[str, str] = None, static_items: List[Config] = None, user_items: List[Config] = None):
        RpcManager.__init__(self, CONFIG_FILE, self.__validate_config_file)
        self.folders = folders if folders is not None else Folders()
        self.static_items = self.__serialize_items(static_items)
        self.user_items = self.__serialize_items(user_items)
        self.cli_config = cli_config if cli_config is not None else {}

        # Can't support an item in both lists
        conflicting_items = list(set(self.static_items.keys()) & set(self.user_items.keys()))
        if len(conflicting_items):
            raise RpcException("Some config items defined as both static and user ones: " + ", ".join(conflicting_items), ResultCode.ERROR_MODEL_INVALID)

        # Prepare default/current values dict
        defaults = self.__load_defaults()
        currents = self._load_config(self.folders.workspace)

        # Load default/current values for all items
        for name, item in self.__all_items.items():
            # Validate and set default value
            default_val = defaults[name]
            item.validate(name, default_val)
            item.item.default_value = default_val

            # Validate persisted current (validator may have changed)
            if name in currents:
                current_val = currents[name]
                try:
                    item.update(current_val)
                    continue
                except Exception:
                    self.logger.warning(f"Can't load invalid persisted value '{current_val}' for config item {name}; use default one")

            # Default (no persisted current value or validation error on persisted value)
            item.update(default_val)

    def _load(self):
        # Simple dump of all loaded items
        self.logger.info("Items dump on load:")
        for item_type, name, item in [
            (item_type, name, item) for item_type, item_map in [("static", self.static_items), ("user", self.user_items)] for name, item in item_map.items()
        ]:
            self.logger.info(f" - [{item_type}] {name}: {item.str_val} (default: {item.default_value})")

    def __serialize_items(self, input_list: list) -> Dict[str, Config]:
        # Browse input list items, that may be:
        # - either a Config instance
        # - or a ConfigHolder containing a list of Config instances
        out = {}
        if input_list is not None:
            for candidate in input_list:
                if isinstance(candidate, Config):
                    # Simply add to output
                    out[candidate.name] = candidate
                else:
                    # Assume this is a ConfigHolder class: serialize all items from the holder
                    out.update({n: i for n, i in map(lambda i: (i.name, i), candidate.all_config_items())})
        return out

    @property
    def __all_items(self) -> Dict[str, Config]:
        out = dict(self.static_items)
        out.update(self.user_items)
        return out

    def __validate_config_file(self, config_file: Path, json_model):
        if not isinstance(json_model, dict) or any(not isinstance(v, str) for v in json_model.values()):
            raise RpcException(f"Invalid config json file (expecting a simple str:str object): {config_file}", ResultCode.ERROR_MODEL_INVALID)

    def __load_env_config(self) -> Dict[str, str]:
        # Check if configuration item default value is provided by environment
        env_defaults = {}
        for name in self.__all_items.keys():
            # Transform to env var name --> env var for foo_bar_12 config name is FOO_BAR_12
            env_name = name.upper().replace("-", "_")
            if env_name in os.environ:
                env_defaults[name] = os.environ[env_name]
        return env_defaults

    def __load_defaults(self) -> Dict[str, str]:
        # Layer 1: hard-coded values
        defaults = {i.name: i.hard_coded_default_value for i in self.__all_items.values()}
        self.logger.debug(f"Loading defaults (hard-coded): {defaults}")

        # Layer 2: system shared config file
        defaults.update(self._load_config(self.folders.system))
        self.logger.debug(f"Loading defaults (from system config at {self.folders.system}): {defaults}")

        # Layer 3: user config file
        defaults.update(self._load_config(self.folders.user))
        self.logger.debug(f"Loading defaults (from user config at {self.folders.user}): {defaults}")

        # Layer 4: environment
        defaults.update(self.__load_env_config())
        self.logger.debug(f"Loading defaults (from environment): {defaults}")

        # Layer 5: command-line
        defaults.update(self.cli_config)
        self.logger.debug(f"Loading defaults (from cli options): {defaults}")

        return defaults

    def __persist(self):
        # Persist non-default public values
        self._save_config({i.name: i.str_val for i in filter(lambda i: i.str_val != i.default_value, self.user_items.values())})

    def __check_items(self, input_names: list, items_to_check: dict, ignore_unknown: bool, empty_ok: bool = False):
        # Check for empty list
        if not empty_ok and len(input_names) == 0:
            raise RpcException("Input request list is empty", ResultCode.ERROR_PARAM_MISSING)

        # Check for empty name in list
        if any(n == "" for n in input_names):
            raise RpcException("At least one empty name found in input request", ResultCode.ERROR_PARAM_MISSING)

        # Check filter for unknown items
        unknown_items = list(filter(lambda n: n not in items_to_check.keys(), input_names))
        if not ignore_unknown and len(unknown_items):
            raise RpcException("Unknown config item names in filter request: " + ", ".join(unknown_items), ResultCode.ERROR_ITEM_UNKNOWN)

    def __filter_items(self, names: List[str]) -> List[ConfigItem]:
        return list(map(lambda x: self.user_items[x].item, filter(lambda x: x in self.user_items, names if len(names) else self.user_items.keys())))

    def __merged_items(self, names: List[str], check_conflicts: bool = False) -> Dict[str, ConfigItem]:
        # Delegate to all proxied servers + merge with local items
        merged_items = {}
        dump_all_filter = Filter(names=names, ignore_unknown=True)

        # Dump items from proxied clients
        items_dumps = [self.__filter_items(names)]
        for client in self.__proxied_config_clients:
            self.logger.debug(f"Dump items from remote ({client.target_host})")
            items_dumps.append(client.config.get(dump_all_filter).items)

        # Iterate on dumps
        for items_dump in items_dumps:
            # Merge proxied items
            for item in items_dump:
                if item.name in merged_items:
                    if item.value != merged_items[item.name].value and check_conflicts:
                        # Conflict between services; needs to be raised as an error
                        raise RpcException(
                            f"Proxied values conflict for config item {item.name}: {item.value} != {merged_items[item.name].value}",
                            rc=ResultCode.ERROR_ITEM_CONFLICT,
                        )
                    else:
                        self.logger.debug(f"Item {item.name} (value: {item.value}) already merged; keep previous value ({merged_items[item.name].value})")
                else:
                    # Grab this proxied item
                    merged_items[item.name] = item
                    self.logger.debug(f"Item {item.name} merged (value: {item.value})")
        return merged_items

    def get(self, request: Filter) -> ConfigStatus:
        """
        Get configuration items, according to input filter
        """

        with self.lock:
            # Basic checks
            merged_items = self.__merged_items(request.names, True)
            self.__check_items(request.names, merged_items, request.ignore_unknown, True)

            # Return filtered list
            return ConfigStatus(items=merged_items.values())

    def reset(self, request: Filter) -> ConfigStatus:
        """
        Reset configuration items, according to input filter
        """

        with self.lock:
            # Basic checks
            merged_items = self.__merged_items(request.names, False)
            self.__check_items(request.names, merged_items, request.ignore_unknown)

            # Delegate to proxied servers
            for client in self.__proxied_config_clients:
                self.logger.debug(f"Reset items on remote ({client.target_host})")
                client.config.reset(Filter(names=request.names, ignore_unknown=True))

            # Reset all local items to their default values
            for name in filter(lambda n: n in self.user_items, request.names):
                self.user_items[name].reset()

            # Get again to build returned values
            merged_items = self.__merged_items(request.names, True)
            return ConfigStatus(items=merged_items.values())

    def set(self, request: ConfigUpdate) -> ConfigStatus:  # NOQA:A003
        """
        Update configuration items, according to input request
        """

        with self.lock:
            # Basic checks
            req_map = {r.name: r.value for r in request.items}
            merged_items = self.__merged_items(req_map.keys(), False)
            self.__check_items(req_map.keys(), merged_items, request.ignore_unknown)

            # Validate local items new values
            for name, value in filter(lambda tpl: tpl[0] in self.user_items, req_map.items()):
                self.user_items[name].validate(name, value)

            # Delegate to proxied servers
            for client in self.__proxied_config_clients:
                self.logger.debug(f"Set items on remote ({client.target_host})")
                client.config.set(ConfigUpdate(items=list(request.items), ignore_unknown=True))

            # Finally, update local values
            for name, value in filter(lambda tpl: tpl[0] in self.user_items, req_map.items()):
                self.user_items[name].update(value)

            # Persist updated local values
            self.__persist()

            # Get again to build returned values
            merged_items = self.__merged_items(req_map.keys(), True)
            return ConfigStatus(items=merged_items.values())

    @property
    def __proxied_config_clients(self) -> List[RpcClient]:
        return self._proxied_clients({"config": (ConfigServiceStub, ConfigApiVersion.CONFIG_API_CURRENT)})
