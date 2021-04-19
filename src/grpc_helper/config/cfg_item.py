import re
from typing import Callable, List

from grpc_helper.api import ConfigItem, ConfigValidator, ResultCode
from grpc_helper.errors import RpcException

# Pattern for config item name
NAME_PATTERN = re.compile("[a-z][a-z0-9-]*")


# Integer validation
def validate_int(name: str, value: str):
    try:
        return int(value)
    except ValueError:
        raise RpcException(f"Invalid int value for config item {name}: {value}", rc=ResultCode.ERROR_PARAM_INVALID)


# Positive number validation
def validate_pos(name: str, value: str, type_validator: Callable):
    if type_validator(name, value) <= 0:
        raise RpcException(f"Expected strictly positive value for config item {name} but got {value}", rc=ResultCode.ERROR_PARAM_INVALID)


# Positive integer validation
def validate_pos_int(name: str, value: str):
    validate_pos(name, value, validate_int)


# Float validation
def validate_float(name: str, value: str):
    try:
        return float(value)
    except ValueError:
        raise RpcException(f"Invalid float value for config item {name}: {value}", rc=ResultCode.ERROR_PARAM_INVALID)


# Float validation
def validate_pos_float(name: str, value: str):
    validate_pos(name, value, validate_float)


# Built-Validators
VALIDATORS = {
    ConfigValidator.CONFIG_VALID_STRING: lambda _n, _v: None,
    ConfigValidator.CONFIG_VALID_INT: validate_int,
    ConfigValidator.CONFIG_VALID_POS_INT: validate_pos_int,
    ConfigValidator.CONFIG_VALID_FLOAT: validate_float,
    ConfigValidator.CONFIG_VALID_POS_FLOAT: validate_pos_float,
}


class Config:
    """
    API for configuration items.

    Constructor arguments are the same than the ConfigItem ones, with an additional custom_validator one,
    allowing to define a custom validation method.
    This method must takes two arguments:
    * a "name" string: may be useful to raise meaningful validation errors
    * a "value" string: to be validated. The method shall raise an RpcException if the value is invalid.
    """

    def __init__(self, custom_validator: Callable = None, **kwargs):
        # Create item with kwargs
        self.item = ConfigItem(**kwargs)

        # Preserve hard-coded default (mainly for unit tests, which are re-using instances with modified defaults)
        self.hard_coded_default_value = self.item.default_value

        # Validate name
        m = NAME_PATTERN.match(self.item.name)
        if m is None:
            raise RpcException(f"Invalid config item name: {self.item.name}", ResultCode.ERROR_PARAM_INVALID)

        # Validate validator
        if self.item.validator == ConfigValidator.CONFIG_VALID_CUSTOM and custom_validator is None:
            raise RpcException(f"Missing custom validator for config item: {self.item.name}", ResultCode.ERROR_PARAM_MISSING)

        # Validation operation delegated to configured implementation
        self.__validate = custom_validator if self.item.validator == ConfigValidator.CONFIG_VALID_CUSTOM else VALIDATORS[self.item.validator]

    @property
    def name(self) -> str:
        return self.item.name

    @property
    def default_value(self) -> str:
        return self.item.default_value

    @property
    def str_val(self) -> str:
        return self.item.value

    @property
    def int_val(self) -> int:
        return int(self.str_val)

    @property
    def float_val(self) -> float:
        return float(self.str_val)

    def reset(self):
        # Reset item value to its default one
        self.update(self.item.default_value)

    def update(self, value: str):
        # Validate item before update
        self.validate(self.name, value)

        # Update item value
        self.item.value = value

    def validate(self, name: str, value: str):
        # Can be empty?
        if value == "":
            if not self.item.can_be_empty:
                raise RpcException(f"Empty value provided for item {name} (not supported)", rc=ResultCode.ERROR_PARAM_MISSING)
        else:
            # Delegate to validator
            self.__validate(name, value)


class ConfigHolder:
    """
    A base class from which configuration items holders may inherit
    """

    @classmethod
    def all_config_items(cls) -> List[Config]:
        return list(filter(lambda x: isinstance(x, Config), cls.__dict__.values()))
