from dataclasses import dataclass


@dataclass
class RpcMetadata:
    """
    Data class holding metadata related to an RPC call:
    * client:       Client name (client code)
    * user:         User name running the calling process
    * host:         Host name of the calling process
    * ip:           IP address of the calling process
    * api_version:  API version known to the caller
    """

    client: str = ""
    user: str = ""
    host: str = ""
    ip: str = ""
    api_version: str = ""

    def as_tuple(self) -> tuple:
        # Build tuple from set attributes
        return tuple(filter(lambda x: len(x[1]), self.__dict__.items()))

    @classmethod
    def from_context(cls, context):
        # Parse GRPC execution context
        out = cls()
        context_meta = {k: v for k, v in context.invocation_metadata()}
        for key in filter(lambda x: x in context_meta, out.__dict__.keys()):
            setattr(out, key, context_meta[key])
        return out

    def __str__(self):
        # String representation of metadata
        return (
            f"[{self.client if len(self.client) else 'unknown'}]"
            + f"{self.user if len(self.user) else 'unknown'}"
            + f"@{self.host if len(self.host) else 'unknown'}"
            + f"({self.ip if len(self.ip) else 'unknown'})"
            + f" api:{self.api_version if len(self.api_version) else 0}"
        )
