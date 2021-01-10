from pathlib import Path

MAX_TRACE_BUFFER_LEN = 1024


def __peer_from_rpc(context):
    metadata = {k: v for k, v in context.invocation_metadata()}
    peer = ""
    if len(metadata):  # pragma: no branch
        peer = (
            f"[{metadata['client'] if 'client' in metadata else 'unknown'}]"
            + f"{metadata['user'] if 'user' in metadata else 'unknown'}"
            + f"@{metadata['host'] if 'host' in metadata else 'unknown'}"
            + f"({metadata['ip'] if 'ip' in metadata else 'unknown'})"
            + f" api:{metadata['api_version'] if 'api_version' in metadata else 0}"
        )
    return peer


def trace_buffer(buffer) -> str:
    # Build buffer name and content trace (stripped if too long)
    buffer_str = str(buffer).replace("\n", " ")
    buffer_str = buffer_str if len(buffer_str) < MAX_TRACE_BUFFER_LEN else buffer_str[0:MAX_TRACE_BUFFER_LEN] + "..."
    buffer_name = type(buffer).__name__
    return f"{buffer_name}{{{buffer_str}}}"


def trace_rpc(input_rpc: bool, buffer, context=None, method=None) -> tuple:
    buffer = trace_buffer(buffer)

    if context is not None:
        # Build peer information from context metadata
        peer = __peer_from_rpc(context)

        # Get method name from context
        p = Path(context._rpc_event.call_details.method.decode("utf-8"))
        method = f"{p.parent.name}.{p.name}"
    else:
        # No context = no peer
        peer = ""

    # Build full trace string
    if input_rpc:
        return f"[RPC] {peer} >>> {method} ({buffer})"
    else:
        return f"[RPC] {peer} <<< {method}: {buffer}"
