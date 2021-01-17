import logging
from threading import RLock

from grpc_helper.client import RpcClient


class RpcManager:
    """
    Shared interface for all RPC managers, helping to control the service lifecycle.
    """

    def __init__(self):
        # Prepare some shared attributes
        self.lock = RLock()
        self.client = None
        self.logger = logging.getLogger(type(self).__name__)

    def preload(self, client: RpcClient):
        # Remember client, and delegate loading to subclass
        self.client = client
        self.load()

    def load(self):
        """
        To be defined by sub-classes, if some specific operations have to be performed once all basic initializations are done
        """
        pass

    def shutdown(self):
        """
        To be defined by sub-classes, if some specific operations have to be performed before shutting down the process
        """
        pass
