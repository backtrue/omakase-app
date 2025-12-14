import json
from typing import Any, Dict, Optional


def sse_event(event: str, data: Dict[str, Any], event_id: Optional[str] = None) -> str:
    payload = ""
    if event_id is not None:
        payload += f"id: {event_id}\n"
    payload += f"event: {event}\n"
    payload += f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
    return payload
