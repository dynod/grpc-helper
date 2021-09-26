import time
from queue import Queue

from grpc_helper.api import Empty, Event, EventFilter, EventInterrupt, EventQueueStatus, EventStatus, Result, ResultCode, ResultStatus
from grpc_helper.api.events_pb2_grpc import EventServiceServicer
from grpc_helper.errors import RpcException
from grpc_helper.manager import RpcManager
from grpc_helper.static_config import RpcStaticConfig


class EventsManager(EventServiceServicer, RpcManager):
    """
    Events manager, and implementing the EventService API
    """

    def __init__(self):
        # Super call
        super().__init__()

        # Some setup
        self.__queues = {}
        self.__interrupt_times = {}

    def _shutdown(self):
        # On shutdown, interrupt all pending queues
        with self.lock:
            for q in self.__queues.values():
                q.put(EventStatus(r=Result(msg="Service is shutdown", code=ResultCode.ERROR_STREAM_SHUTDOWN)), False)

    def __get_queue(self, index: int) -> Queue:
        with self.lock:
            q = self.__queues[index] if index in self.__queues else None
            if q is None:
                raise RpcException(f"Unknown event listening ID: {index}", ResultCode.ERROR_ITEM_UNKNOWN)
            return q

    def interrupt(self, request: EventInterrupt) -> ResultStatus:
        # Known index?
        q = self.__get_queue(request.client_id)

        # Refuse to interrupt again already interrupted queue
        with self.lock:
            if request.client_id in self.__interrupt_times and self.__interrupt_times[request.client_id] is not None:
                raise RpcException(f"Already interrupted ID: {request.client_id}", ResultCode.ERROR_STATE_UNEXPECTED)

        # Put "None" event (will wait until listen loop is over)
        q.put(None)
        return ResultStatus()

    def listen(self, request: EventFilter) -> EventStatus:
        # Validate filter
        for name in request.names:
            if len(name) == 0:
                raise RpcException("Empty name in filter request", ResultCode.ERROR_PARAM_MISSING)
            if " " in name:
                raise RpcException(f"Invalid name in filter request: {name}", ResultCode.ERROR_PARAM_INVALID)

        with self.lock:
            # Event queue specified?
            if request.client_id > 0:
                # Try to work with already existing queue
                index = request.client_id
                q = self.__get_queue(index)
                self.logger.info(f"Resuming interrupted event listening queue #{index}")

                # Listening resumed: forget interrupt time
                self.__interrupt_times[index] = None
            else:
                # Prepare a new index (from scratch, to reuse released indexes)
                index = 1
                while index in self.__queues:
                    index += 1
                self.logger.info(f"Starting new event listening queue #{index}")

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
            # Remember interupt time
            interrupt_time = time.time()
            self.__interrupt_times[index] = interrupt_time
            self.logger.info(f"Event listening queue #{index} interrupted")

        # Last event to be yield (in case of shutdown)?
        if isinstance(event, EventStatus):
            yield event

    def send(self, request: Event) -> ResultStatus:
        # Just check event name is correct
        if len(request.name) == 0:
            raise RpcException("Empty event name", ResultCode.ERROR_PARAM_MISSING)
        if " " in request.name:
            raise RpcException(f"Invalid event name: {request.name}", ResultCode.ERROR_PARAM_INVALID)

        with self.lock:
            # Browse all queues
            to_delete = []
            for index in self.__queues.keys():
                # Reckon interrupted time
                interrupted_time = (
                    (time.time() - self.__interrupt_times[index]) if index in self.__interrupt_times and self.__interrupt_times[index] is not None else None
                )
                retain_timeout = RpcStaticConfig.EVENT_RETAIN_TIMEOUT.int_val

                # Interrupted queue + retain timeout expired?
                if interrupted_time is not None and interrupted_time >= retain_timeout:
                    # Yes, forget event queue
                    self.logger.info(f"Deleting interrupted event queue #{index} (retain timeout: {interrupted_time} >= {retain_timeout})")
                    to_delete.append(index)
                else:
                    # Still active queue: push event
                    self.__queues[index].put_nowait(request)

            # Forget timed out events
            for index in to_delete:
                del self.__queues[index]
                del self.__interrupt_times[index]

        return ResultStatus()

    def inspect(self, request: Empty) -> EventQueueStatus:
        # Just dump active queues
        with self.lock:
            return EventQueueStatus(client_ids=list(self.__queues.keys()))
