import time
import traceback
from queue import Queue
from threading import Event as ThreadEvent
from threading import Thread

from grpc_helper_api import Empty, Event, EventFilter, EventInterrupt, EventQueueStatus, EventStatus, Result, ResultCode, ResultStatus
from grpc_helper_api.events_pb2_grpc import EventServiceServicer

from grpc_helper.errors import RpcException
from grpc_helper.folders import Folders
from grpc_helper.manager import RpcManager
from grpc_helper.static_config import RpcStaticConfig


class EventsManager(EventServiceServicer, RpcManager):
    """
    Events manager, and implementing the EventService API
    """

    def __init__(self):
        # Super call
        super().__init__("queues.json")

        # Some setup
        self.__queues = {}
        self.__interrupt_times = {}

        # Init keep alive thread
        self.__keep_alive_stop = ThreadEvent()
        self.__keep_alive_t = None

    def _init_folders_n_logger(self, folders: Folders, port: int):
        # Super call
        super()._init_folders_n_logger(folders, port)

        # Prepare persisted queues
        persisted_q = list(map(int, self._load_config(self.folders.workspace).keys()))
        self.logger.debug(f"Re-creating persisted event queues: {persisted_q}")
        for q_index in persisted_q:
            self.__queues[q_index] = Queue()

            # Also assume all queues are interrupted (i.e. will disappear if listen is not resumed within the retain timeout)
            self.__interrupt_times[q_index] = time.time()

    def _load(self):
        super()._load()

        # Start keep alive thread
        self.__keep_alive_t = Thread(target=self.__keep_alive, daemon=True)
        self.__keep_alive_t.start()

    def _persist_queues(self):
        with self.lock:
            # Persist queues
            self._save_config({i: [] for i in self.__queues.keys()})

    def _shutdown(self):
        with self.lock:
            # Stop keep alive thread
            if self.__keep_alive_t is not None:  # pragma: no branch
                self.__keep_alive_stop.set()
                self.__keep_alive_t.join()

            # Browse pending queues
            for q in self.__queues.values():
                # Push an interrupt status
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
                self._persist_queues()

        # Yield at least a first status with client_id
        yield EventStatus(client_id=index)

        # Loop to flush received events
        while True:
            # Blocking read
            event = q.get()

            # End of loop?
            if event is None or isinstance(event, EventStatus):
                break

            # Is this a meaningful or a keep alive event?
            if len(request.names) == 0 or event.name in request.names or event.name == "":
                # Yield received events (if any)
                yield EventStatus(client_id=index, event=event)

        # Forget queue
        with self.lock:
            # Remember interrupt time
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

        return self.__internal_send(request)

    def __internal_send(self, request: Event) -> ResultStatus:
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

            # Forget timed out events (if any)
            if len(to_delete):
                for index in to_delete:
                    del self.__queues[index]
                    del self.__interrupt_times[index]
                self._persist_queues()

        return ResultStatus()

    def inspect(self, request: Empty) -> EventQueueStatus:
        # Just dump active queues
        with self.lock:
            return EventQueueStatus(client_ids=list(self.__queues.keys()))

    def __keep_alive(self):
        while not self.__keep_alive_stop.is_set():
            try:
                # Send keep alive event
                self.__internal_send(Event())

                # Sleep before looping
                init_time = time.time()
                while (not self.__keep_alive_stop.is_set()) and (time.time() - init_time) < RpcStaticConfig.EVENT_KEEPALIVE_TIMEOUT.int_val:
                    time.sleep(1)
            except Exception as e:  # pragma: no cover
                self.logger.error(f"Exception while sending keep alive event: {e}\n" + "".join(traceback.format_tb(e.__traceback__)))
