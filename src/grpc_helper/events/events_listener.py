import traceback
from abc import ABC, abstractmethod
from logging import Logger, getLogger
from threading import Event as ThreadEvent
from threading import Thread
from typing import List

from grpc_helper.api import Event, EventFilter, EventInterrupt, ResultCode
from grpc_helper.client import RpcClient
from grpc_helper.errors import RpcException


class EventsListener(ABC):
    """
    Abstract event listener, with resume handling

    Arguments:
        client:
            Rpc client with an "events" stub
        names:
            Event names to listen to.
        client_id:
            Persisted events listening ID (if any)
        logger:
            Logger instance (optional)
    """

    def __init__(self, client: RpcClient, names: List[str], client_id: int = None, logger: Logger = None):

        # Init some stuff
        self.client = client
        self.names = names
        self.logger = logger if logger is not None else getLogger(EventsListener.__name__)
        self.client_id = client_id
        self.ready = ThreadEvent()

        # Prepare listening thread
        self.listening_t = Thread(target=self.__listen_to_events, daemon=True)
        self.listening_t.start()

    def __listen_to_events(self):
        # Listening loop
        while True:
            try:
                for s in self.client.events.listen(EventFilter(client_id=self.client_id, names=self.names)):
                    # Remember ID
                    if not self.ready.is_set():
                        self.client_id = s.client_id
                        self.logger.debug(f"Event listener #{self.client_id} is ready")
                        self.ready.set()

                    # Something to notify?
                    if len(s.event.name):
                        self.logger.debug(f">> Event listener #{self.client_id} on_event({s.event.name})")
                        self.on_event(s.event)
                        self.logger.debug(f"<< Event listener #{self.client_id} on_event({s.event.name})")

                # Listen loop normal exit: listening was interrupted
                break
            except Exception as e:
                error_trace = (
                    "event service restarting"
                    if isinstance(e, RpcException) and e.rc == ResultCode.ERROR_STREAM_SHUTDOWN
                    else f"{e}\n" + "".join(traceback.format_tb(e.__traceback__))
                )
                self.logger.error(f"Error occurred in event listener #{self.client_id} internal loop: {error_trace}")

    @abstractmethod
    def on_event(self, event: Event):  # pragma: no cover
        """
        To be implemented by sub-classes; will be called on any received event
        """
        pass

    def interrupt(self):
        """
        Interrupts listening loop
        """
        self.logger.debug(f">> Interrupting event listener #{self.client_id}")
        self.client.events.interrupt(EventInterrupt(client_id=self.client_id))
        self.listening_t.join()
        self.logger.debug(f"<< Interrupting event listener #{self.client_id}")
