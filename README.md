# grpc-helper

![Tests](https://github.com/dynod/gprc-helper/workflows/Tests/badge.svg)

Miscellaneous GRPC helpers (API versioning, retry, etc...)

## Python API

Provided classes in this module API help to deal with RPC servers/clients handling.

### RpcServer

The **`RpcServer`** class handles the lifecycle of a GRPC server. To initialize, it basically needs:
* a port on which to server RPC requests
* a list of **`RpcServiceDescriptor`** objects

The **`RpcServiceDescriptor`** class describes a given service to be hooked in a server instance. Its attributes are:
* a Python module (from which name and version will be used for the InfoService items registration)
* a version enum:
   * lowest value in the enum will be considered to be the minimum supported version for this service API
   * highest value in the enum will be considered to be the current version for this service API
* a manager instance, to which all RPC calls will be delegated
* the GRPC generated hooking method for this service

The manager class must:
* inherit from the GRPC generated servicer class, in order to use the default implementation if any of the service method is not implemented by this manager
* for each implemented service method:
   * declare a single input parameter, which will be the input request
   * declare the return type

#### Behavior

The RPC server will live its life in its own thread. When the application is about to terminate, it is advised to call the **`shutdown`** method
in order to turn off the RPC server properly.

Note that the RPC server instance will automatically serves the [info service](doc/info.md), giving information about all installed services thanks to
the provided descriptors.

For requests received from the **`RpcClient`** implementation on a given service, the client api version is checked against the server "supported - current" 
range for this service:
* if the client version is older than the server supported version, the request will return a **`ResultCode.ERROR_API_CLIENT_TOO_OLD`** error
* if the client version is newer than the server current version, the request will return a **`ResultCode.ERROR_API_SERVER_TOO_OLD`** error

#### Usage example

```python
import my_package
from my_package.api import MyStatus, MyConfig, MyApiVersion
from my_package.api.my_pb2_grpc import add_MyServiceServicer_to_server, MyServiceServicer
from grpc_helper import RpcServer, RpcServiceDescriptor

class MySampleManager(MyServiceServicer):
    # Custom implementation for sample service
    
    def update(self, request: MyConfig) -> MyStatus:
        # Note that return message *MUST* be explicitely annoted for each implemented method!
        return MyStatus()

def start():
    # Create an RPC server on port 12345
    srv = RpcServer(12345, [RpcServiceDescriptor(my_package, MyApiVersion, MySampleManager(), add_MyServiceServicer_to_server)])

    # Server is running in its own thread; we need to wait forever (or for interruption event) here
    ...

    # Shutdown the server
    srv.shutdown()
```

### RpcClient

The **`RpcClient`** class provides an access to client side of a GRPC service. To initialize, it basically needs:
* a host name or IP address for the RPC server to connect to
* a port for the RPC server
* a map of service stubs to access:
   * keys are used to define fields names on the generated client object, holding the client stubs
   * values are tuple providing:
      * the GRPC generated stub name
      * the client current API version (coming from the version enum)

Optional inputs can also be provided:
* a timeout if RPC server is unreachable (default: 60s)
* a name allowing to identify the client on the server side

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


## GRPC API
This module defines an [info service](doc/info.md) that can be used to fetch services/components information
