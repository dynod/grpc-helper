import time
from queue import Empty, Queue

from grpc_helper import RpcClient, RpcException
from grpc_helper.api import Empty as EmptyMessage
from grpc_helper.api import Event, EventFilter, EventInterrupt, EventProperty, ResultCode
from grpc_helper.events import EventsListener
from grpc_helper.static_config import RpcStaticConfig
from tests.utils import TestUtils


class SomeEventListener(EventsListener):
    def __init__(self, client: RpcClient, client_id: int = None, rec_queue: Queue = None):
        super().__init__(client, ["some-event"], client_id)
        self.rec_queue = Queue() if rec_queue is None else rec_queue

    def on_event(self, event: Event):
        # Append received event
        self.rec_queue.put_nowait(event)


class TestEvents(TestUtils):
    def test_listen_missing_params(self, client):
        # Try with bad filters
        try:
            for _ in client.events.listen(EventFilter(names=[""])):
                pass
            raise AssertionError("shouldn't get here")
        except RpcException as e:
            assert e.rc == ResultCode.ERROR_PARAM_MISSING

    def test_listen_bad_params(self, client):
        # Try with bad filters
        try:
            for _ in client.events.listen(EventFilter(names=["with space"])):
                pass
            raise AssertionError("shouldn't get here")
        except RpcException as e:
            assert e.rc == ResultCode.ERROR_PARAM_INVALID

    def test_listen_unknown(self, client):
        # Try with unknown client
        try:
            for _ in client.events.listen(EventFilter(client_id=135)):
                pass
            raise AssertionError("shouldn't get here")
        except RpcException as e:
            assert e.rc == ResultCode.ERROR_ITEM_UNKNOWN

    def test_send_missing_params(self, client):
        # Try with bad event
        try:
            client.events.send(Event(name=""))
            raise AssertionError("shouldn't get here")
        except RpcException as e:
            assert e.rc == ResultCode.ERROR_PARAM_MISSING

    def test_send_bad_params(self, client):
        # Try with bad event
        try:
            client.events.send(Event(name="with space"))
            raise AssertionError("shouldn't get here")
        except RpcException as e:
            assert e.rc == ResultCode.ERROR_PARAM_INVALID

    def test_interrupt_unknown(self, client):
        # Try with unknown client
        try:
            client.events.interrupt(EventInterrupt())
            raise AssertionError("shouldn't get here")
        except RpcException as e:
            assert e.rc == ResultCode.ERROR_ITEM_UNKNOWN

    def start_listening(self, client, index: int = None, rec_queue: Queue = None) -> SomeEventListener:
        # Start listener
        listener = SomeEventListener(client, index, rec_queue)
        listener.ready.wait()
        return listener

    def test_events_workflow(self, client):
        # Start listening threads
        listener1 = self.start_listening(client)
        listener2 = self.start_listening(client)

        # Send first event
        client.events.send(Event(name="some-event", properties=[EventProperty(name="foo", value="bar")]))

        # Send another event (shouldn't be received)
        client.events.send(Event(name="another-event"))

        received_events_map = {}
        for listener in [listener1, listener2]:
            # Verify inspect contains id
            assert listener.client_id in client.events.inspect(EmptyMessage()).client_ids

            # Interrupt
            listener.interrupt()

            # Verify inspect still contains id
            assert listener.client_id in client.events.inspect(EmptyMessage()).client_ids

            # Try to interrupt again (expecting bad state)
            try:
                listener.interrupt()
                raise AssertionError("shouldn't get here")
            except RpcException as e:
                assert e.rc == ResultCode.ERROR_STATE_UNEXPECTED

            # Join listening thread and flush all received events
            received_events_map[listener.client_id] = self.flush_queue(listener.rec_queue)

        # Check received events
        for received_events in received_events_map.values():
            assert len(received_events) == 1
            event = received_events[0]
            assert event.name == "some-event"
            assert len(event.properties) == 1
            assert event.properties[0].name == "foo"
            assert event.properties[0].value == "bar"

    def flush_queue(self, q: Queue) -> list:
        out = []
        while True:
            try:
                out.append(q.get_nowait())
            except Empty:
                break
        return out

    def test_listen_shutdown(self, client):
        # Start listening
        listener = self.start_listening(client)

        # Shutdown service
        self.shutdown_server_instance()

        # Expects no status
        assert len(self.flush_queue(listener.rec_queue)) == 0

        # Restart service
        self.new_server_instance()

        # Send event
        client.events.send(Event(name="some-event"))

        # Interrupt
        listener.interrupt()

        # Expect message to be received
        assert len(self.flush_queue(listener.rec_queue)) == 1

    def test_inspect_empty(self, client):
        # By default, no running event loops
        s = client.events.inspect(EmptyMessage())
        assert len(s.client_ids) == 0

    def test_resume(self, client):
        # Start listening
        listener = self.start_listening(client)

        # Send one before interrupting
        client.events.send(Event(name="some-event"))

        # Interrupt
        listener.interrupt()

        # Send one while interrupted
        client.events.send(Event(name="some-event"))

        # Resume
        listener = self.start_listening(client, listener.client_id, listener.rec_queue)

        # Send one after resume
        client.events.send(Event(name="some-event"))

        # Final interrupt
        RpcStaticConfig.EVENT_RETAIN_TIMEOUT.update("1")
        listener.interrupt()

        # Wait for retain timeout to expire
        time.sleep(1.5)

        # Send one after timeout
        client.events.send(Event(name="some-event"))

        # Interrupt again (should be unknown)
        try:
            listener.interrupt()
            raise AssertionError("shouldn't get here")
        except RpcException as e:
            assert e.rc == ResultCode.ERROR_ITEM_UNKNOWN

        # Check final event count
        events = self.flush_queue(listener.rec_queue)
        assert len(events) == 3
        assert all(e.name == "some-event" for e in events)

    def test_keep_alive(self, client):
        # Short keep alive timeout
        RpcStaticConfig.EVENT_KEEPALIVE_TIMEOUT.update("1")

        # Start listening
        listener = self.start_listening(client)

        # Sleep a bit more than keep alive timeout
        time.sleep(1.5)

        # Interrupt
        listener.interrupt()

        # No event received
        assert len(self.flush_queue(listener.rec_queue)) == 0

        # But keep alive event has been received
        self.check_logs("<<< EventService.listen: EventStatus{event { } client_id: 1 }")
