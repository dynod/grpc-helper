import socket
from pathlib import Path

from grpc._channel import _StreamStreamMultiCallable, _UnaryStreamMultiCallable

from grpc_helper.meta import RpcMetadata

MAX_TRACE_BUFFER_LEN = 1024


def trace_buffer(buffer) -> str:
    # Build buffer name and content trace (stripped if too long)
    buffer_str = str(buffer).replace("\n", " ")
    buffer_str = buffer_str if len(buffer_str) < MAX_TRACE_BUFFER_LEN else buffer_str[0:MAX_TRACE_BUFFER_LEN] + "..."
    buffer_name = type(buffer).__name__
    return f"{buffer_name}{{{buffer_str}}}"


def trace_rpc(input_rpc: bool, buffer, context=None, method=None) -> tuple:
    buffer = trace_buffer(buffer)

    if isinstance(context, RpcMetadata):
        # Context is directly set as an RpcMetadata instance
        peer = str(context)
    else:
        # Build peer information from context metadata
        peer = str(RpcMetadata.from_context(context))

        # Get method name from context
        p = Path(context._rpc_event.call_details.method.decode("utf-8"))
        method = f"{p.parent.name}.{p.name}"

    # Build full trace string
    if input_rpc:
        return f"[RPC] {peer} >>> {method} ({buffer})"
    else:
        return f"[RPC] {peer} <<< {method}: {buffer}"


def is_streaming(stub: object, n: str):
    # Check if method is streaming output
    stub_method = getattr(stub, n)
    return isinstance(stub_method, _UnaryStreamMultiCallable) or isinstance(stub_method, _StreamStreamMultiCallable)


def get_current_ip(default: str = "127.0.0.1"):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 1))
        out = s.getsockname()[0]
    except Exception:  # pragma: no cover
        out = default
    finally:
        s.close()
    return out
