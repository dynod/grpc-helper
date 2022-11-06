import json
import os
from pathlib import Path

import pytest
from grpc_helper_api import ConfigItemUpdate, ConfigUpdate, ConfigValidator, Filter, ProxyRegisterRequest, ResultCode

from grpc_helper import Folders, RpcException
from grpc_helper.config import Config, ConfigHolder
from grpc_helper.config.cfg_manager import ConfigManager
from grpc_helper.server import RpcStaticConfig
from tests.utils import TestUtils


class SampleConfig(ConfigHolder):
    INT_ITEM = Config(name="my-int-config", description="sample int configuration", default_value="12", validator=ConfigValidator.CONFIG_VALID_INT)


class Sample2Config(ConfigHolder):
    INT_ITEM = Config(name="my-int-config", description="sample int configuration", default_value="12", validator=ConfigValidator.CONFIG_VALID_INT)


class TestConfig(TestUtils):
    @pytest.fixture
    def system_config(self):
        # Sample system config folder+file
        sys_path = self.test_folder / "system"
        sys_config = sys_path / "config.json"
        sys_path.mkdir()
        with sys_config.open("w") as f:
            json.dump({"my-int-config": "-78"}, f)
        yield sys_path

    @pytest.fixture
    def user_config(self):
        # Sample user config folder+file
        usr_path = self.test_folder / "user"
        usr_config = usr_path / "config.json"
        usr_path.mkdir()
        with usr_config.open("w") as f:
            json.dump({"my-int-config": "1024"}, f)
        yield usr_path

    @pytest.fixture
    def env_config(self):
        # Populate value in environment
        os.environ["MY_INT_CONFIG"] = "456"

        yield

        # Clean env
        del os.environ["MY_INT_CONFIG"]

    def test_invalid_config_name(self):
        try:
            # Invalid config name
            ConfigManager(folders=self.folders, static_items=[Config(name="WithCapitals")])
            raise AssertionError("Shouldn't get here")
        except RpcException as e:
            assert e.rc == ResultCode.ERROR_PARAM_INVALID

    def test_missing_custom_validator(self):
        try:
            # Missing custom validator in config definition
            ConfigManager(folders=self.folders, static_items=[Config(name="ok", validator=ConfigValidator.CONFIG_VALID_CUSTOM)])
            raise AssertionError("Shouldn't get here")
        except RpcException as e:
            assert e.rc == ResultCode.ERROR_PARAM_MISSING

    def test_empty_default(self):
        # Config item with empty default
        cm = ConfigManager(folders=self.folders, static_items=[Config(name="ok", can_be_empty=True)])
        assert cm.static_items["ok"].str_val == ""

    def test_float_validation(self):
        try:
            # Non-float value
            ConfigManager(
                folders=self.folders, static_items=[Config(name="some-float", validator=ConfigValidator.CONFIG_VALID_FLOAT, default_value="not a float")]
            )
            raise AssertionError("Shouldn't get here")
        except RpcException as e:
            assert e.rc == ResultCode.ERROR_PARAM_INVALID

    def test_pos_float_validation(self):
        try:
            # Negative float while expecting a positive one
            ConfigManager(
                folders=self.folders, static_items=[Config(name="some-pos-float", validator=ConfigValidator.CONFIG_VALID_POS_FLOAT, default_value="-69.45")]
            )
            raise AssertionError("Shouldn't get here")
        except RpcException as e:
            assert e.rc == ResultCode.ERROR_PARAM_INVALID

        # Same with correct default value
        ConfigManager(static_items=[Config(name="some-pos-float", validator=ConfigValidator.CONFIG_VALID_POS_FLOAT, default_value="69.45")])

    def test_pos_int_validation(self):
        try:
            # Negative integer while expecting a positive one
            ConfigManager(folders=self.folders, static_items=[Config(name="some-pos-int", validator=ConfigValidator.CONFIG_VALID_POS_INT, default_value="-69")])
            raise AssertionError("Shouldn't get here")
        except RpcException as e:
            assert e.rc == ResultCode.ERROR_PARAM_INVALID

        # Same with correct default value
        ConfigManager(static_items=[Config(name="some-pos-int", validator=ConfigValidator.CONFIG_VALID_POS_INT, default_value="69")])

    def test_string_validation(self):
        # Default string validation
        ConfigManager(folders=self.folders, static_items=[Config(name="some-string", default_value="Any default is OK")])

    def test_hard_coded_default(self):
        # A simple config manager
        ConfigManager(folders=self.folders, static_items=[SampleConfig])

        # Check default value
        assert SampleConfig.INT_ITEM.int_val == 12

    def test_system_default(self, system_config):
        # A simple config manager
        ConfigManager(static_items=[SampleConfig], folders=Folders(workspace=self.workspace_path, system=system_config))

        # Check default value
        assert SampleConfig.INT_ITEM.int_val == -78

    def test_user_default(self, system_config, user_config):
        # A simple config manager
        ConfigManager(static_items=[SampleConfig], folders=Folders(workspace=self.workspace_path, system=system_config, user=user_config))

        # Check default value
        assert SampleConfig.INT_ITEM.int_val == 1024

    def test_env_default(self, system_config, user_config, env_config):
        # A simple config manager
        ConfigManager(static_items=[SampleConfig], folders=Folders(workspace=self.workspace_path, system=system_config, user=user_config))

        # Check default value
        assert SampleConfig.INT_ITEM.int_val == 456

    def test_cli_default(self, system_config, user_config, env_config):
        # A simple config manager
        ConfigManager(
            static_items=[SampleConfig],
            folders=Folders(workspace=self.workspace_path, system=system_config, user=user_config),
            cli_config={"my-int-config": "357"},
        )

        # Check default value
        assert SampleConfig.INT_ITEM.int_val == 357

    def test_folder_without_file_default(self):
        # A simple config manager -- shouldn't be disturbed is folder is non-null, but no config file inside
        ConfigManager(static_items=[SampleConfig], folders=Folders(workspace=self.workspace_path, system=Path("/missing/folder")))

        # Check default value
        assert SampleConfig.INT_ITEM.int_val == 12

    def test_invalid_json_model(self, system_config):
        # Dump non-object Json in config
        with (system_config / "config.json").open("w") as f:
            f.write('{"unclosed item')
        try:
            ConfigManager(static_items=[SampleConfig], folders=Folders(workspace=self.workspace_path, system=system_config))
            raise AssertionError("Shouldn't get here")
        except RpcException as e:
            assert e.rc == ResultCode.ERROR_MODEL_INVALID

        # Dump object with non-string values Json in config
        with (system_config / "config.json").open("w") as f:
            json.dump({"some-int": 45}, f)
        try:
            ConfigManager(static_items=[SampleConfig], folders=Folders(workspace=self.workspace_path, system=system_config))
            raise AssertionError("Shouldn't get here")
        except RpcException as e:
            assert e.rc == ResultCode.ERROR_MODEL_INVALID

    def test_conflicting_configs(self):
        # Try with some conflicting items between statis and user lists
        try:
            ConfigManager(folders=self.folders, static_items=[SampleConfig], user_items=[Config(name="my-int-config", default_value="zzz")])
            raise AssertionError("Shouldn't get here")
        except RpcException as e:
            assert e.rc == ResultCode.ERROR_MODEL_INVALID

    def test_invalid_default(self):
        # Try with invalid default value
        try:
            ConfigManager(folders=self.folders, static_items=[SampleConfig], cli_config={"my-int-config": "zzz"})
            raise AssertionError("Shouldn't get here")
        except RpcException as e:
            assert e.rc == ResultCode.ERROR_PARAM_INVALID

    def test_rpc_server_config(self, sample_server):
        # Verify inner RPC server config (values initialized)
        assert RpcStaticConfig.MAX_WORKERS.int_val == 30

    @property
    def user_items(self) -> list:
        return [SampleConfig]

    @property
    def user_items2(self) -> list:
        return [Sample2Config]

    def test_get_empty(self, client):
        # Try to get items with empty request
        try:
            client.config.get(Filter(names=[""]))
            raise AssertionError("Shouldn't get here")
        except RpcException as e:
            assert e.rc == ResultCode.ERROR_PARAM_MISSING

    def test_get_unknown(self, client):
        # Try to get unknown item
        try:
            client.config.get(Filter(names=["unknown"]))
            raise AssertionError("Shouldn't get here")
        except RpcException as e:
            assert e.rc == ResultCode.ERROR_ITEM_UNKNOWN

    def test_get_ok(self, client):
        # Get item
        s = client.config.get(Filter())
        assert len(s.items) == 1
        item = s.items[0]
        assert item.name == "my-int-config"
        assert item.value == "12"

    def test_set_empty_list(self, client):
        # Try to set with an empty request
        try:
            client.config.set(ConfigUpdate())
            raise AssertionError("Shouldn't get here")
        except RpcException as e:
            assert e.rc == ResultCode.ERROR_PARAM_MISSING

    def test_set_empty_value(self, client):
        # Try to set with an empty value while not authorized
        try:
            client.config.set(ConfigUpdate(items=[ConfigItemUpdate(name="my-int-config")]))
            raise AssertionError("Shouldn't get here")
        except RpcException as e:
            assert e.rc == ResultCode.ERROR_PARAM_MISSING

    def test_set_bad_value(self, client):
        # Try to set with a bad value
        try:
            client.config.set(ConfigUpdate(items=[ConfigItemUpdate(name="my-int-config", value="foo")]))
            raise AssertionError("Shouldn't get here")
        except RpcException as e:
            assert e.rc == ResultCode.ERROR_PARAM_INVALID

    def test_set_ok(self, client):
        # Verify file is not persisted yet
        wks = self.test_folder / "wks"
        cfg = wks / "config.json"
        assert not cfg.is_file()

        # Set new value
        s = client.config.set(ConfigUpdate(items=[ConfigItemUpdate(name="my-int-config", value="999")]))
        assert len(s.items) == 1
        item = s.items[0]
        assert item.name == "my-int-config"
        assert item.value == "999"

        # File is persisted
        assert cfg.is_file()

        # Read again to make sure :)
        s = client.config.get(Filter(names=["my-int-config"]))
        assert len(s.items) == 1
        item = s.items[0]
        assert item.name == "my-int-config"
        assert item.value == "999"

        # Reload to verify persistence
        self.shutdown_server_instance()
        self.new_server_instance()

        # Read again
        s = client.config.get(Filter(names=["my-int-config"]))
        assert len(s.items) == 1
        item = s.items[0]
        assert item.name == "my-int-config"
        assert item.value == "999"

        # Reload to verify ignored persistence if no workspace (but update logs folder anyway)
        self.shutdown_server_instance()
        os.environ["RPC_LOGS_FOLDER"] = (self.test_folder / "custom_log_full_path").as_posix()
        self.new_server_instance(with_workspace=False)
        cfg.unlink()

        # Set new value; will not be persisted
        s = client.config.set(ConfigUpdate(items=[ConfigItemUpdate(name="my-int-config", value="888")]))
        assert not cfg.is_file()

        # Write an invalid persisted value
        with cfg.open("w") as f:
            json.dump({"my-int-config": "invalid string"}, f)

        # Reload to verify invalid value being ignored (and restore default logs folder)
        self.shutdown_server_instance()
        del os.environ["RPC_LOGS_FOLDER"]
        self.new_server_instance()
        self.check_logs("Can't load invalid persisted value 'invalid string' for config item my-int-config")

        # Read again (should be default value)
        s = client.config.get(Filter(names=["my-int-config"]))
        assert len(s.items) == 1
        item = s.items[0]
        assert item.name == "my-int-config"
        assert item.value == "12"

    def test_reset(self, client):
        # Read
        s = client.config.get(Filter(names=["my-int-config"]))
        assert len(s.items) == 1
        item = s.items[0]
        assert item.name == "my-int-config"
        assert item.value == "12"

        # Set new value
        s = client.config.set(ConfigUpdate(items=[ConfigItemUpdate(name="my-int-config", value="777")]))

        # Read
        s = client.config.get(Filter(names=["my-int-config"]))
        assert len(s.items) == 1
        item = s.items[0]
        assert item.name == "my-int-config"
        assert item.value == "777"

        # Reset
        s = client.config.reset(Filter(names=["my-int-config"]))

        # Read
        s = client.config.get(Filter(names=["my-int-config"]))
        assert len(s.items) == 1
        item = s.items[0]
        assert item.name == "my-int-config"
        assert item.value == "12"

    def test_proxy_config_get(self, proxy_server, client, another_server):
        # Register proxies
        proxy_server.client.srv.proxy_register(ProxyRegisterRequest(names=["sample"], version="123", port=self.rpc_port))

        # Read (all values shall be the same)
        for c in (client, another_server.client, proxy_server.client):
            s = c.config.get(Filter(names=["my-int-config"]))
            assert len(s.items) == 1
            item = s.items[0]
            assert item.name == "my-int-config"
            assert item.value == "12"

    def test_proxy_config_set_n_reset(self, proxy_server, client, another_server):
        # Register proxies
        proxy_server.client.srv.proxy_register(ProxyRegisterRequest(names=["sample"], version="123", port=self.rpc_port))

        # Set in proxy
        s = proxy_server.client.config.set(ConfigUpdate(items=[ConfigItemUpdate(name="my-int-config", value="789")]))
        assert len(s.items) == 1
        item = s.items[0]
        assert item.name == "my-int-config"
        assert item.value == "789"

        # Read (all values shall be the same)
        for c in (client, another_server.client, proxy_server.client):
            s = c.config.get(Filter(names=["my-int-config"]))
            assert len(s.items) == 1
            item = s.items[0]
            assert item.name == "my-int-config"
            assert item.value == "789"

        # Reset
        s = proxy_server.client.config.reset(Filter(names=["my-int-config"]))
        assert len(s.items) == 1
        item = s.items[0]
        assert item.name == "my-int-config"
        assert item.value == "12"

        # Read (all values shall be the same)
        for c in (client, another_server.client, proxy_server.client):
            s = c.config.get(Filter(names=["my-int-config"]))
            assert len(s.items) == 1
            item = s.items[0]
            assert item.name == "my-int-config"
            assert item.value == "12"

    def test_proxy_config_conflict(self, proxy_server, client, another_server):
        # Register proxies
        proxy_server.client.srv.proxy_register(ProxyRegisterRequest(names=["sample"], version="123", port=self.rpc_port))

        # Update only on one proxied server
        s = another_server.client.config.set(ConfigUpdate(items=[ConfigItemUpdate(name="my-int-config", value="1024")]))
        assert len(s.items) == 1
        item = s.items[0]
        assert item.name == "my-int-config"
        assert item.value == "1024"

        # Read while values are different in proxied servers: conflict
        try:
            proxy_server.client.config.get(Filter(names=["my-int-config"]))
            raise AssertionError("shouldn't get here")
        except RpcException as e:
            assert e.rc == ResultCode.ERROR_ITEM_CONFLICT
