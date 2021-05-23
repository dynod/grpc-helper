from queue import Queue

from grpc_helper.api import Event, EventInterrupt, EventStatus, Filter, ResultCode, ResultStatus
from grpc_helper.api.common_pb2 import Result
from grpc_helper.api.events_pb2_grpc import EventServiceServicer
from grpc_helper.errors import RpcException
from grpc_helper.manager import RpcManager


class EventsManager(EventServiceServicer, RpcManager):
    """
    Events manager, and implementing the EventService API
    """

    def __init__(self):
        # Super call
        super().__init__()

        # Some setup
        self.__queues = {}

    def _shutdown(self):
        # On shutdown, interrupt all pending queues
        with self.lock:
            for q in self.__queues.values():
                q.put(EventStatus(r=Result(msg="Service is shutdown", code=ResultCode.ERROR_STREAM_SHUTDOWN)), False)

    def __get_queue(self, index: int) -> Queue:
        with self.lock:
            return self.__queues[index] if index in self.__queues else None

    def interrupt(self, request: EventInterrupt) -> ResultStatus:
        # Known index?
        q = self.__get_queue(request.client_id)
        if q is None:
            raise RpcException(f"Unknown event listening ID: {request.client_id}", ResultCode.ERROR_ITEM_UNKNOWN)

        # Put "None" event (will wait until listen loop is over)
        q.put(None)
        return ResultStatus()

    def listen(self, request: Filter) -> EventStatus:
        # Validate filter
        for name in request.names:
            if len(name) == 0:
                raise RpcException("Empty name in filter request", ResultCode.ERROR_PARAM_MISSING)
            if " " in name:
                raise RpcException(f"Invalid name in filter request: {name}", ResultCode.ERROR_PARAM_INVALID)

        # Prepare a new queue
        with self.lock:
            # Prepare a new index (from scratch, to reuse released indexes)
            index = 1
            while index in self.__queues:
                index += 1

            # Create new queue
            q = Queue()
            self.__queues[index] = q

        # Yield at least a first status with client_id
        yield EventStatus(client_id=index)

        # Loop to flush received events
        while True:
            # Blocking read
            event = q.get()

            # End of loop?
            if event is None or isinstance(event, EventStatus):
                break

            # Is this a meaningful event?
            if len(request.names) == 0 or event.name in request.names:
                # Yield received events (if any)
                yield EventStatus(client_id=index, event=event)

        # Forget queue
        with self.lock:
            del self.__queues[index]

        # Last event to be yield (in case of shutdown)?
        if isinstance(event, EventStatus):
            yield event

    def send(self, request: Event) -> ResultStatus:
        # Just check event name is correct
        if len(request.name) == 0:
            raise RpcException("Empty event name", ResultCode.ERROR_PARAM_MISSING)
        if " " in request.name:
            raise RpcException(f"Invalid event name: {request.name}", ResultCode.ERROR_PARAM_INVALID)

        # Add event to all queues
        with self.lock:
            for q in self.__queues.values():
                q.put_nowait(request)

        return ResultStatus()
