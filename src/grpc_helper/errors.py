from grpc_helper_api import ResultCode


class RpcException(Exception):  # NOQA: N818
    """
    Common error class, holding an error code (typically to be returned in RPC response messages)
    """

    def __init__(self, message: str, rc: int = ResultCode.ERROR):
        super().__init__(message)
        self.rc = rc
