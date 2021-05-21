from grpc_helper.api import ConfigValidator, ResultCode
from grpc_helper.config.cfg_item import Config, ConfigHolder
from grpc_helper.errors import RpcException

# Allowed interval units
INTERVAL_UNITS = ["S", "M", "H", "D", "MIDNIGHT"] + list(map(lambda x: f"W{x}", range(7)))


# Interval unit validation
def validate_interval_unit(name: str, value: str):
    if value.upper() not in INTERVAL_UNITS:
        raise RpcException(f"Invalid interval unit for {name}: {value}", rc=ResultCode.ERROR_PARAM_INVALID)


# Internal config for RPC servers
class RpcStaticConfig(ConfigHolder):
    MAX_WORKERS = Config(
        name="rpc-max-workers", description="Maximum parallel RPC worker threads", default_value="30", validator=ConfigValidator.CONFIG_VALID_POS_INT
    )
    SHUTDOWN_GRACE = Config(
        name="rpc-shutdown-grace",
        description="Grace period for pending calls to be terminated on shutdown (seconds)",
        default_value="30",
        validator=ConfigValidator.CONFIG_VALID_POS_FLOAT,
    )
    SHUTDOWN_TIMEOUT = Config(
        name="rpc-shutdown-timeout",
        description="Final timeout before real shutdown (i.e. end of process; seconds)",
        default_value="60",
        validator=ConfigValidator.CONFIG_VALID_POS_FLOAT,
    )
    LOGS_FOLDER = Config(name="rpc-logs-folder", description="Workspace relative folder where to store rolling logs", default_value="logs")
    LOGS_BACKUP = Config(
        name="rpc-logs-backup",
        description="Backup log files to be persisted for each manager on rollover",
        default_value="10",
        validator=ConfigValidator.CONFIG_VALID_INT,
    )
    LOGS_ROLLOVER_INTERVAL_UNIT = Config(
        name="rpc-logs-interval-unit",
        description="Rollover interval unit (see TimedRotatingFileHandler documentation)",
        default_value="H",
        validator=ConfigValidator.CONFIG_VALID_CUSTOM,
        custom_validator=validate_interval_unit,
    )
    LOGS_ROLLOVER_INTERVAL = Config(
        name="rpc-logs-interval",
        description="Rollover interval (see TimedRotatingFileHandler documentation)",
        default_value="1",
        validator=ConfigValidator.CONFIG_VALID_POS_INT,
    )
    MAIN_HOST = Config(name="rpc-main-host", description="Main RPC server host (to be used by proxied services)", default_value="localhost")
    MAIN_PORT = Config(
        name="rpc-main-port",
        description="Main RPC server port (to be used by proxied services)",
        default_value="54321",
        validator=ConfigValidator.CONFIG_VALID_POS_INT,
    )
    CLIENT_TIMEOUT = Config(
        name="rpc-client-timeout",
        description="Timeout for RPC client when server is unreachable or proxy not registered yet (seconds)",
        default_value="60",
        validator=ConfigValidator.CONFIG_VALID_POS_FLOAT,
    )
