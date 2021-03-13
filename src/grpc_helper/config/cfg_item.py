import re
from typing import Callable

from grpc_helper.api import ConfigItem, ConfigValidator, ResultCode
from grpc_helper.errors import RpcException

# Pattern for config item name
NAME_PATTERN = re.compile("[a-z][a-z0-9-]*")


# Integer validation
def validate_int(value: str):
    try:
        int(value)
    except ValueError:
        raise RpcException(f"Invalid int value for {value}")


# Positive integer validation
def validate_pos_int(value: str):
    validate_int(value)
    if int(value) <= 0:
        raise RpcException(f"Expected strictly positive value but got {value}")


# Built-Validators
VALIDATORS = {
    ConfigValidator.CONFIG_VALID_STR: lambda: None,
    ConfigValidator.CONFIG_VALID_INT: validate_int,
    ConfigValidator.CONFIG_VALID_POS_INT: validate_pos_int,
}


class ConfigInnerItem:
    """
    Inner model for configuration items
    """

    def __init__(self, item: ConfigItem, custom_validator: Callable = None):
        # Validate name
        m = NAME_PATTERN.match(item.name)
        if m is None:
            raise RpcException(f"Invalid config item name: {item.name}", ResultCode.ERROR_PARAM_INVALID)

        # Validate validator
        if item.validator == ConfigValidator.CONFIG_VALID_CUSTOM and custom_validator is None:
            raise RpcException(f"Missing custom validator for config item: {item.name}", ResultCode.ERROR_PARAM_MISSING)

        # Store item
        self.item = item

        # Validation operation delegated to configure implementation
        self.validate = custom_validator if item.validator == ConfigValidator.CONFIG_VALID_CUSTOM else VALIDATORS[item.validator]

    @property
    def name(self) -> str:
        return self.item.name

    def reset(self):
        # Reset item value to its default one
        self.item.value = self.item.default_value
