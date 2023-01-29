# grpc-helper

<!-- NMK-BADGES-BEGIN -->
[![License: MPL](https://img.shields.io/github/license/dynod/grpc-helper?color=green)](https://github.com/dynod/grpc-helper/blob/main/LICENSE)
[![Checks](https://img.shields.io/github/actions/workflow/status/dynod/grpc-helper/build.yml?branch=main&label=build%20%26%20u.t.)](https://github.com/dynod/grpc-helper/actions?query=branch%3Amain)
[![Issues](https://img.shields.io/github/issues-search/dynod/grpc-helper?label=issues&query=is%3Aopen+is%3Aissue)](https://github.com/dynod/grpc-helper/issues?q=is%3Aopen+is%3Aissue)
[![Supported python versions](https://img.shields.io/badge/python-3.8%20--%203.11-blue)](https://www.python.org/)
[![PyPI](https://img.shields.io/pypi/v/grpc-helper)](https://pypi.org/project/grpc-helper/)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)
[![Flake8 analysis result](https://img.shields.io/badge/flake8-0-green)](https://flake8.pycqa.org/)
[![Code coverage](https://img.shields.io/codecov/c/github/dynod/grpc-helper)](https://app.codecov.io/gh/dynod/grpc-helper)
[![Code generated for GRPC framework](https://img.shields.io/badge/code%20generation-proto-blue)](https://protobuf.dev/)
<!-- NMK-BADGES-END -->

Miscellaneous GRPC helpers (API versioning, retry, config, etc...)

## Python API

Provided classes in this module API help to deal with RPC servers/clients handling.

---
### RPC Server

The **`RpcServer`** class handles the lifecycle of a GRPC server. To initialize, it basically needs:
* a port on which to server RPC requests
* a list of **`RpcServiceDescriptor`** objects
* an optional **`Folders`** instance (usually provided by the **`RpcCliParser`** CLI parsing)
* an optional dict of string:string, providing config items defaults initialized from CLI (usually provided by the **`RpcCliParser`** CLI parsing)
* an optional list of **`Config`** or **`ConfigHolder`** instances, which will be this server's *static* config items
* an optional list of **`Config`** or **`ConfigHolder`** instances, which will be this server's *user* config items
* a boolean flag stating if the events service has to be started in this server instance (default: **`False`**)
* a boolean flag stating if the debug signal has to be listened (see below; default: **`True`**)

The **`Folders`** class handle the different folders used by the server:
* a *system* folder, used to store config shared by multiple users and applications. This folder doesn't need to be writable by the server running user.
* a *user* folder, used to store config shared by multiple applications for the server running user.
* a *workspace* folder, used to store config and other persistent information by the current server.

The **`RpcServiceDescriptor`** class describes a given service to be hooked in a server instance. Its attributes are:
* a Python module (from which name and version will be used for the RpcServerService items registration)
* a service name (used for service identification in RpcServerService items, and for auto-client setup)
* a version enum:
   * lowest value in the enum will be considered to be the minimum supported version for this service API
   * highest value in the enum will be considered to be the current version for this service API
* a manager instance, to which all RPC calls will be delegated. When registering a proxied service, this has to be a simple instance of the generated servicer.
* the GRPC generated hooking method for this service
* the GRPC generated client stub class
* a boolean stating if this service is a proxied service or not


The manager class must:
* inherit from the GRPC generated servicer class, in order to use the default implementation if any of the service method is not implemented by this manager
* for each implemented service method:
   * declare a single input parameter, which will be the input request
   * declare the return type

A manager class can also inherit from **`RpcManager`** class, which provides some usefull features:
* a **`_load`** method, called by the server once all services are alive (typically to perform some internal initializations)
* a **`_shutdown`** method, called by the server once it is shutdown (typically to perform some internal finalization operations + interrupt long-running ones)
* a **`logger`** instance, to be used for all logging inside this manager and dependencies
* a **`lock`** instance, to be used to protect manager inner fields against reentrance
* a **`client`** instance, initialized to the server own auto-client (see below)
* a **`folders`** instance, initialized to the server **`Folders`** (see above)

#### Lifecycle

The RPC server will live its life in its own thread. When the application is about to terminate, it is advised to call the **`shutdown`** method
in order to turn off the RPC server properly. This method can also be called remotely as a **RpcServerService** servive method.

When the shutdown method is called:
* the server will stop accepting new requests
* it will notify all managers through their own **`_shutdown`** method
* then it will wait for pending operations to terminate (within the **`rpc-shutdown-grace`** configured timeout)method.
* if called through RPC, if will wait for the required timeout (using by default the **`rpc-shutdown-timeout`** value), if this one is >0
* then the shutdown will be finalized (all non-daemon threads terminated)

Note that the code which created the server instance may use the **`wait_shutdown`** method to block until the RPC server is shutdown.

#### Default services

Note that the RPC server instance will automatically serve:
* the [server handling service](https://github.com/dynod/grpc-helper-api/blob/main/doc/server.md),
for basic RPC server operations handling.
* the [config service](https://github.com/dynod/grpc-helper-api/blob/main/doc/config.md),
allowing to remotely get/update/reset user configuration items.
* the [logger service](https://github.com/dynod/grpc-helper-api/blob/main/doc/logger.md),
allowing to remotely get/update/reset loggers configuration.

#### API version checks

For requests received from the **`RpcClient`** implementation on a given service, the client api version is checked against the server "supported - current" 
range for this service:
* if the client version is older than the server supported version, the request will raise a **`ResultCode.ERROR_API_CLIENT_TOO_OLD`** error
* if the client version is newer than the server current version, the request will raise a **`ResultCode.ERROR_API_SERVER_TOO_OLD`** error

#### Debug

The RPC server will hook to the USR2 signal for debug purpose (if required). When receiving this signal, following debug information will be dumped in a file
name **RpcServerDump-YYYYMMDDhhmmss.txt** (with dump timestamp) in the logging folder (see below):
* all the live threads call stacks
* all the pending RPC requests

#### Auto-client

An initialized RPC server instance provides an **`auto_client`** attribute, providing an **`RpcClient`** instance pointing on everything
served by this server. Note that the timeout for this client can be configured through the **`rpc-client-timeout`** config item.

#### Proxied services

Services declared as proxied ones (see **`RpcServiceDescriptor`** above) will be forwarded to the required server/port as soon as this server calls the 
**`proxy_register`** method of the proxying server. Note that if this method is not called within the **`rpc-client-timeout`** time, the method call will
end with an **`ERROR_PROXY_UNREGISTERED`** error.

Configuration of proxied services is persisted in the workspace, in order to restore them when the proxying server restarts.

An **`RpcProxiedManager`** abstract class is available for managers who are designed to be proxied. Such managers will automatically register in main proxy
server, with following details:
* registered services names list has to be returned by **`_proxied_services`** method
* registered services version string has to be returned by **`_proxied_version`** method
* extra stubs available in the **`self.proxy_client`** attribute can be provided by **`_proxy_stubs`** method
* if current host IP needs excpilicitely to be used for registration, **`_proxy_use_current_host`** method needs to return **`True`**

When handling proxy servers, if the proxying server is started with events service enabled, it will send:
* an **`RPC_PROXY_REGISTER`** event on each **`proxy_register`** method successful call
* an **`RPC_PROXY_FORGET`** event on each **`proxy_forget`** method successful call

Properties of the events will be inherited from the method input message.

#### Rolling logs

Each manager of the RPC server through its **`logger`** attribute, will have his logs persisted in a **\<name\>/\<name\>.log** rolling file (relative to the 
logging folder). The complete logs will also be persisted in the **root** folder.
Both logging folder and rollover cadency can be configured -- see **Configuration** chapter below.

#### Configuration

The RPC server behavior can be configured through the following static configuration items:
Name | Description | Type | Default
---- | ----------- | ---- | -------
**rpc-max-workers**             | Maximum parallel RPC worker threads                                   | Positive integer | **`30`**
**rpc-shutdown-grace**          | Grace period for pending calls to be terminated on shutdown (seconds) | Positive float   | **`30`**
**rpc-shutdown-timeout**        | Final timeout before real shutdown (i.e. end of process; seconds)     | Positive float   | **`60`**
**rpc-logs-folder**             | Folder (absolute or workspace-relative) where to store rolling logs   | String           | **`"logs"`**
**rpc-logs-backup**             | Backup log files to be persisted for each manager on rollover         | Integer          | **`10`**
**rpc-logs-interval-unit**      | Logs rollover interval unit (see [TimedRotatingFileHandler documentation](https://docs.python.org/3/library/logging.handlers.html#logging.handlers.TimedRotatingFileHandler))      | Custom (see [doc](https://docs.python.org/3/library/logging.handlers.html#logging.handlers.TimedRotatingFileHandler)) | **`H`**
**rpc-logs-interval**           | Logs rollover interval (see [TimedRotatingFileHandler documentation](https://docs.python.org/3/library/logging.handlers.html#logging.handlers.TimedRotatingFileHandler)) | Positive integer | **`1`**
**rpc-main-host**               | Main RPC server host (to be used by proxied services)                 | String           | **`"localhost"`**
**rpc-main-port**               | Main RPC server port (to be used by proxied services)                 | Positive integer | **`54321`**
**rpc-client-timeout**          | Timeout for RPC client when server is unreachable or proxy not registered yet (seconds) | Positive float   | **`60`**
**rpc-event-retain-timeout**    | Retain timeout for event queues on interruption (seconds)             | Positive integer | **`300`**
**rpc-event-keepalive-timeout** | Timeout for sending keep alive empty events (seconds)                 | Positive integer | **`60`**

#### Usage example

```python
import my_package
from my_package.api import MyStatus, MyConfig, MyApiVersion
from my_package.api.my_pb2_grpc import add_MyServiceServicer_to_server, MyServiceServicer, MyServiceStub
from grpc_helper import RpcServer, RpcServiceDescriptor, RpcManager

class MySampleManager(MyServiceServicer, RpcManager):
    # Custom implementation for sample service
    
    def update(self, request: MyConfig) -> MyStatus:
        # Note that return message *MUST* be explicitely annoted for each implemented method!
        return MyStatus()

def start():
    # Create an RPC server on port 12345
    srv = RpcServer(12345, [RpcServiceDescriptor(my_package, "my", MyApiVersion, MySampleManager(), add_MyServiceServicer_to_server, MyServiceStub)])

    # Server is running in its own thread; we need to wait it for shutdown (on Ctrl-C or remote shutdown call)
    srv.wait_shutdown()
```

---
### RPC Client

The **`RpcClient`** class provides an access to client side of a GRPC service. To initialize, it basically needs:
* a host name or IP address for the RPC server to connect to
* a port for the RPC server
* a map of service stubs to access:
   * keys are used to define fields names on the generated client object, holding the client stubs
   * values are tuple providing:
      * the GRPC generated stub name
      * the client current API version (coming from the version enum)

Optional inputs can also be provided:
* a timeout if RPC server is unreachable (default: **60s**)
* a name allowing to identify the client on the server side
* a boolean flag stating if the client shall raise exceptions when receiving non-OK **`ResultCode`** status (default: **true**)
* an exception type to be raised (instead of **`RpcException`**) when receiving non-OK **`ResultCode`** status

Once created, a client instance provide as many stubs as configured in the service map. Each of this stubs expose the generated methods of the 
corresponding service. These methods take the following arguments:
* the method input request message (see corresponding proto file)
* a timeout parameter (in seconds) for this particular request. If set to None (default), there is no timeout

#### Usage example

```python
from my_package.api import MyStatus, MyApiVersion, Empty
from my_package.api.my_pb2_grpc import MyServiceStub
from grpc_helper import RpcClient

def start():
    # Get a client access to my service
    c = RpcClient("127.0.0.1", 12345, {"my": (MyServiceStub, MyApiVersion.MY_API_CURRENT)}, name="myclient")

    # Use API
    s: MyStatus = c.my.list(Empty())
```

---
### Configuration items

Instances of the **`Config`** class can be provided to initialize list of static and user configuration items known to a RPC server:
* *static* items are immutable, and are only initialized once when the RPC server instance is created
* *user* items can be modified remotely through the [config service](https://github.com/dynod/grpc-helper-api/blob/main/doc/config.md)

The **`Config`** constructor arguments are the same than the public API **`ConfigItem`** ones (see [config service](https://github.com/dynod/grpc-helper-api/blob/main/doc/config.md)).
It also supports an additional **`custom_validator`** one, allowing to provide a validation method when **`validator`** argument is set to **`CONFIG_VALID_CUSTOM`**.
This method takes two arguments:
* a **`name`** string: may be useful to raise meaningful validation errors
* a **`value`** string: value to be validated. The method shall raise an **`RpcException`** if the value is invalid.

#### Default value

Configuration items default value are loaded in the following order:
1. hard-coded value (the one provided in the item constructor)
2. system folder **config.json** file value (if system folder is configured)
3. user folder **config.json** file value (if user folder is configured)
4. environment provided value. Environment variable name for a given item is obtained by capitalizing it and replacing hyphens ("-") by underscores ("_").
E.g. the environment variable name for a **my-own-setting** config item will be **MY_OWN_SETTING**.
5. **-c / --config** command-line option provided value

Note that if the default value fails to be validated with a given item validator (or if it is empty while not allowed), the server will refuse to launch with a
thrown relevant exception.

#### Current value

Static items current value will be initialized with the default value. User items are initialized to either the default value, or to the user configured value
(thanks to the [config service](https://github.com/dynod/grpc-helper-api/blob/main/doc/config.md)) if it was ever modified. User modified values are
persisted in the workspace folder **config.json** file.

Current value may be programmatically accessed through one of the following property accessors:
* **`item.str_val`**: raw string value
* **`item.int_val`**: value converted as an integer

#### Config holders

Configuration items may be defined as attributes of a class inheriting from the **`ConfigHolder`** one. Such classes can be provided directly to **`RpcServer`**
constructor to initialize configuration items (which is more convenient that referencing items one by one).

---
### Command-line options

A ready-to-use CLI parser is provided through the **`RpcCliParser`** class, allowing to rapidly instantiate an RPC server from CLI options.
Constructor arguments are:
* a **`description`** string, which will be displayed when using the **-h / --help** option
* an optional **`version`** string, which will be displayed when using the **-V / --version** option

#### Options

The options defined by this parser are:
Name | Description
---- | -----------
**--help** | displays the program help and exit
**-V / --version** | displays the program version and exit
**--system PATH** | overrides the default system folder
**--user PATH** | overrides the default user folder
**-w / --workspace PATH** | defines the workspace folder
**-p / --port PORT** | overrides the RPC server default listening port
**-c / --config NAME=VALUE** | overrides the default value of a given configuration item

After parsing:
* the system/user/workspace folders are used to initialize a **`Folders`** object (see above), available in the **`folders`** argument of the namespace.
* the configuration items default values are gathered in a dict, available in the **`config`** argument of the namespace.

#### Usage

The following methods are available to tune the parser behavior:
* **`with_rpc_args`**: adds the options listed above, and returns the **`RpcCliParser`** instance (for fluent API usage). Arguments are:
  * **`default_port`**: default RPC listening port
  * **`default_sys`**: default system folder
  * **`default_usr`**: default user folder
  * **`default_wks`**: default workspace folder
* **`parse`**: will parse input arguments, and return the namespace. Arguments are program command-line ones by default, or may be provided as a string list
(e.g. for testing purpose)

Following example shows how to use this parser to create an **`RpcServer`** instance:
```python
from grpc_helper import RpcCliParser, RpcServer

def start():
    # Parse arguments to create RPC server instance
    args = RpcCliParser("My custom RPC server", "1.0.0").with_rpc_args(12345, "/etc/my_srv", "~/.local/my_srv", "my_srv_wks").parse()
    srv = RpcServer(args.port, [], args.folders, args.config)
```
