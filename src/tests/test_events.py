import threading
import time
from queue import Empty, Queue

from grpc_helper.api import Event, EventInterrupt, EventProperty, Filter, ResultCode
from grpc_helper.api.events_pb2 import EventStatus
from grpc_helper.errors import RpcException
from tests.utils import TestUtils


class TestEvents(TestUtils):
    def test_listen_missing_params(self, client):
        # Try with bad filters
        try:
            for _ in client.events.listen(Filter(names=[""])):
                pass
            raise AssertionError("shouldn't get here")
        except RpcException as e:
            assert e.rc == ResultCode.ERROR_PARAM_MISSING

    def test_listen_bad_params(self, client):
        # Try with bad filters
        try:
            for _ in client.events.listen(Filter(names=["with space"])):
                pass
            raise AssertionError("shouldn't get here")
        except RpcException as e:
            assert e.rc == ResultCode.ERROR_PARAM_INVALID

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

    def listening_thread(self, client, start_event: threading.Event, rec_queue: Queue):
        # Ready to listen
        start_event.set()
        try:
            for s in client.events.listen(Filter(names=["some-event"])):
                rec_queue.put_nowait(s)
        except RpcException as e:
            rec_queue.put_nowait(e)

    def start_listening(self, client):
        # Start listening thread
        start_event = threading.Event()
        rec_queue = Queue()
        t = threading.Thread(target=self.listening_thread, kwargs={"client": client, "start_event": start_event, "rec_queue": rec_queue})
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

            # Interrupt
            client.events.interrupt(EventInterrupt(client_id=s.client_id))

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
