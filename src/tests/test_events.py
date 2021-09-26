import threading
import time
from queue import Empty, Queue

from grpc_helper.api import Empty as EmptyMessage
from grpc_helper.api import Event, EventFilter, EventInterrupt, EventProperty, ResultCode
from grpc_helper.api.events_pb2 import EventStatus
from grpc_helper.errors import RpcException
from grpc_helper.static_config import RpcStaticConfig
from tests.utils import TestUtils


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

    def listening_thread(self, client, start_event: threading.Event, rec_queue: Queue, index: int, except_while_listening: bool):
        try:
            # Listen to events
            for s in client.events.listen(EventFilter(names=["some-event"], client_id=index)):
                start_event.set()
                rec_queue.put_nowait(s)

                # Interrupt with exception is required
                if len(s.event.name) and except_while_listening:
                    raise Exception("Some unknown exception")
        except RpcException as e:
            rec_queue.put_nowait(e)

    def start_listening(self, client, index: int = None, q: Queue = None, except_while_listening: bool = False):
        # Start listening thread
        start_event = threading.Event()
        rec_queue = Queue() if q is None else q
        t = threading.Thread(
            target=self.listening_thread,
            kwargs={"client": client, "start_event": start_event, "rec_queue": rec_queue, "index": index, "except_while_listening": except_while_listening},
        )
        t.start()
        start_event.wait()
        time.sleep(0.2)
        return (rec_queue, t)

    def test_events_workflow(self, client):
        # Start listening threads
        rec_queue1, t1 = self.start_listening(client)
        rec_queue2, t2 = self.start_listening(client)

        # Send first event
        client.events.send(Event(name="some-event", properties=[EventProperty(name="foo", value="bar")]))

        # Send another event (shouldn't be received)
        client.events.send(Event(name="another-event"))

        received_events_map = {}
        for rec_queue, t in [(rec_queue1, t1), (rec_queue2, t2)]:
            # Get at least message 1 (containing only client_id)
            s = rec_queue.get(timeout=10)
            assert isinstance(s, EventStatus)
            assert s.client_id > 0
            assert s.event.name == ""

            # Verify inspect contains id
            assert s.client_id in client.events.inspect(EmptyMessage()).client_ids

            # Interrupt
            client.events.interrupt(EventInterrupt(client_id=s.client_id))

            # Verify inspect still contains id
            assert s.client_id in client.events.inspect(EmptyMessage()).client_ids

            # Try to interrupt again (expecting bad state)
            try:
                client.events.interrupt(EventInterrupt(client_id=s.client_id))
                raise AssertionError("shouldn't get here")
            except RpcException as e:
                assert e.rc == ResultCode.ERROR_STATE_UNEXPECTED

            # Join listening thread and flush all received events
            t.join()
            received_events_map[s.client_id] = self.flush_queue(rec_queue)

        # Check received events
        for received_events in received_events_map.values():
            assert len(received_events) == 1
            s = received_events[0]
            assert s.r.code == ResultCode.OK
            event = s.event
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
        rec_queue, t = self.start_listening(client)

        # Simulate service shutdown
        self.server.descriptors["events"].manager._shutdown()

        # Listening thread should stop
        t.join()
        all_status = self.flush_queue(rec_queue)

        # Expects only one status, with "interrupted" code
        assert len(all_status) == 2
        e = all_status[1]
        assert isinstance(e, RpcException)
        assert e.rc == ResultCode.ERROR_STREAM_SHUTDOWN

    def test_inspect_empty(self, client):
        # By default, no running event loops
        s = client.events.inspect(EmptyMessage())
        assert len(s.client_ids) == 0

    def test_resume(self, client):
        # Start listening
        rec_queue, t = self.start_listening(client)

        # Get client ID
        s = rec_queue.get()
        index = s.client_id

        # Send one before interrupting
        client.events.send(Event(name="some-event"))

        # Interrupt
        client.events.interrupt(EventInterrupt(client_id=index))
        t.join()

        # Send one while interrupted
        client.events.send(Event(name="some-event"))

        # Resume
        _, t = self.start_listening(client, index, rec_queue)

        # Send one after resume
        client.events.send(Event(name="some-event"))

        # Final interrupt
        RpcStaticConfig.EVENT_RETAIN_TIMEOUT.update("1")
        client.events.interrupt(EventInterrupt(client_id=index))
        t.join()

        # Wait for retain timeout to expire
        time.sleep(1.5)

        # Send one after timeout
        client.events.send(Event(name="some-event"))

        # Interrupt again (should be unknown)
        try:
            client.events.interrupt(EventInterrupt(client_id=index))
            raise AssertionError("shouldn't get here")
        except RpcException as e:
            assert e.rc == ResultCode.ERROR_ITEM_UNKNOWN

        # Check final event count
        events = self.flush_queue(rec_queue)
        assert len(events) == 4  # one with resume id (no event) + 3 really received ones
        assert all(e.client_id == index for e in events)
        with_events = list(filter(lambda e: e.event.name != "", events))
        assert len(with_events) == 3
        assert all(e.event.name == "some-event" for e in with_events)

    def test_client_interrupt(self, client):
        # Start listening (asking to kill listening loop on client side)
        rec_queue, t = self.start_listening(client, except_while_listening=True)

        # Send event
        client.events.send(Event(name="some-event"))

        # Thread shall be terminated
        t.join()

        # Send event (should be interrupted)
        client.events.send(Event(name="some-event"))

        # Flush events (2 expected)
        events = self.flush_queue(rec_queue)
        assert len(events) == 2
