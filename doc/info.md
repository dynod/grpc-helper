# Info service

The info service (defined in [info.proto](../protos/grpc_helper/api/info.proto)) allows to fetch information about available services for this RPC server.


---
## get

**`rpc get (Empty) returns (MultiServiceInfo);`**

#### *Behavior*
List known available services information.

#### *Return*
A **`MultiServiceInfo`** message, including information for each available service:
* "module.service" name and version
* current and supported API version
