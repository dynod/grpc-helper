import json
import logging
import os
import time

from grpc_helper import RpcException
from grpc_helper.api import Filter, LoggerConfig, LoggerLevel, LoggerUpdate, ResultCode
from tests.utils import TestUtils


class TestLogs(TestUtils):
    @property
    def loggers_file(self):
        return self.workspace_path / "loggers.json"

    def test_invalid_json_log_file(self):
        # Prepare invalid user log file
        self.workspace_path.mkdir(parents=True, exist_ok=True)

        # Not a json file
        with self.loggers_file.open("w") as f:
            f.write('{"invalid json')
        try:
            self.new_server_instance()
            raise AssertionError("Shouldn't get here")
        except RpcException as e:
            assert e.rc == ResultCode.ERROR_MODEL_INVALID

    def test_invalid_dict_log_file(self):
        # Prepare invalid user log file
        self.workspace_path.mkdir(parents=True, exist_ok=True)

        # Json file, but with a dict containing invalid types
        with self.loggers_file.open("w") as f:
            json.dump({"logger1": False, "logger2": "DEBUG", "logger3": 123}, f)
        try:
            self.new_server_instance()
            raise AssertionError("Shouldn't get here")
        except RpcException as e:
            assert e.rc == ResultCode.ERROR_MODEL_INVALID

    def test_persisted_level(self):
        # Prepare persisted level file
        self.workspace_path.mkdir(parents=True, exist_ok=True)
        with self.loggers_file.open("w") as f:
            json.dump({"logger1": False, "logger2": "DEBUG", "logger3": "FOO"}, f)

        # Load server
        self.new_server_instance()

        # Verify "unknown level" trace
        self.check_logs("Ignoring unknown level FOO for logger logger3")

        # Verify configured level
        c = self.server.client
        names = ["logger1", "logger2", "logger3"]
        s = c.log.get(Filter(names=names))
        assert s.r.code == ResultCode.OK
        assert len(s.items) == len(names)
        for lc in s.items:
            assert lc.name in names
            if lc.name == "logger1":
                assert not lc.enabled
                assert lc.level == LoggerLevel.LVL_UNKNOWN
            if lc.name == "logger2":
                assert lc.enabled
                assert lc.level == LoggerLevel.LVL_DEBUG
            if lc.name == "logger3":
                assert lc.enabled
                assert lc.level == LoggerLevel.LVL_UNKNOWN

        # Shutdown server
        self.server.shutdown()

    def test_get_missing_params(self, client):
        # Try with bad filters
        try:
            client.log.get(Filter())
            raise AssertionError("shouldn't get here")
        except RpcException as e:
            assert e.rc == ResultCode.ERROR_PARAM_MISSING

    def test_get_custom(self, client):
        # Try with default
        ln = "sample_logger"
        s = client.log.get(Filter(names=[ln]))
        assert len(s.items) == 1
        lc = s.items[0]
        assert lc.name == ln
        assert lc.enabled
        assert lc.level == LoggerLevel.LVL_UNKNOWN

        # Check known level
        logging.getLogger(ln).setLevel(logging.WARNING)
        s = client.log.get(Filter(names=[ln]))
        assert s.items[0].level == LoggerLevel.LVL_WARNING

        # Check unknown level
        logging.getLogger(ln).setLevel(12)
        s = client.log.get(Filter(names=[ln]))
        assert s.items[0].level == LoggerLevel.LVL_UNKNOWN

    def test_get_root(self, client):
        # Try with root logger
        s = client.log.get(Filter(names=[""]))
        assert len(s.items) == 1
        lc = s.items[0]
        assert lc.name == "root"
        assert lc.enabled
        assert lc.level == LoggerLevel.LVL_DEBUG  # Because of pytest initialization; in production, should be Warning by default

    def test_set_and_reset(self, client):
        # Set and read back configuration
        ln = "updated_logger"

        # Default
        s = client.log.get(Filter(names=[ln]))
        assert len(s.items) == 1
        lc = s.items[0]
        assert lc.name == ln
        assert lc.enabled
        assert lc.level == LoggerLevel.LVL_UNKNOWN
        assert not self.loggers_file.is_file()

        # Set
        s = client.log.set(LoggerUpdate(items=[LoggerConfig(name=ln, enabled=True, level=LoggerLevel.LVL_WARNING)]))
        lc = s.items[0]
        assert lc.name == ln
        assert lc.enabled
        assert lc.level == LoggerLevel.LVL_WARNING
        assert self.loggers_file.is_file()

        # Verify model
        with self.loggers_file.open("r") as f:
            model = json.load(f)
        assert len(model) == 1
        assert model[ln] == "WARNING"

        # Get
        s = client.log.get(Filter(names=[ln]))
        lc = s.items[0]
        assert lc.name == ln
        assert lc.enabled
        assert lc.level == LoggerLevel.LVL_WARNING

        # Reset
        s = client.log.reset(Filter(names=[ln]))
        lc = s.items[0]
        assert lc.name == ln
        assert lc.enabled
        assert lc.level == LoggerLevel.LVL_UNKNOWN
        assert self.loggers_file.is_file()

        # Verify model
        with self.loggers_file.open("r") as f:
            model = json.load(f)
        assert len(model) == 0

        # Get
        s = client.log.get(Filter(names=[ln]))
        lc = s.items[0]
        assert lc.name == ln
        assert lc.enabled
        assert lc.level == LoggerLevel.LVL_UNKNOWN

    def test_reset_unset(self, client):
        # Resetting unmodified logger should not be persisted
        client.log.reset(Filter(names=["unmodified"]))
        assert not self.loggers_file.is_file()

    def test_invalid_interval_unit(self):
        # Try by setting an invalid interval unit
        os.environ["RPC_LOGS_INTERVAL_UNIT"] = "foo"
        try:
            self.new_server_instance()
            raise AssertionError("shouldn't get here")
        except RpcException as e:
            assert e.rc == ResultCode.ERROR_PARAM_INVALID
        del os.environ["RPC_LOGS_INTERVAL_UNIT"]

    def test_rolling_logs(self):
        # Verify rolling is working OK with very short rolling interval
        self.workspace_path.mkdir()
        with (self.workspace_path / "config.json").open("w") as f:
            json.dump({"rpc-logs-interval-unit": "s", "rpc-logs-interval": "1"}, f)

        # Create server
        self.new_server_instance()

        # Loop to generate some logs
        init = time.time()
        while time.time() - init < 3:
            self.server.client.log.get(Filter(names=[""]))

        # Shutdown
        self.server.shutdown()

        # Verify several logs files are generated
        log_files = list((self.workspace_path / "logs" / "LogsManager").glob("LogsManager.log*"))
        logging.debug("Found log files:\n" + "\n".join(map(lambda p: p.as_posix(), log_files)))
        assert len(log_files) >= 3
