"""Deterministic ID generation for replay mode.

In replay mode, generates IDs based on content hashing instead of uuid4.
In normal mode, delegates to uuid4.

Usage:
  from shared.deterministic_id import make_id
  signal_id = make_id("signal", symbol="BTC/USDT", timestamp=...)
  order_id  = make_id("order", signal_id=signal_id, seq=1)
"""

import hashlib
import json
import uuid
from datetime import datetime
from typing import Any

_REPLAY_MODE = False


def enable_replay_mode():
    global _REPLAY_MODE
    _REPLAY_MODE = True


def disable_replay_mode():
    global _REPLAY_MODE
    _REPLAY_MODE = False


def make_id(prefix: str, **fields: Any) -> str:
    if _REPLAY_MODE:
        raw = json.dumps({"p": prefix, **fields}, sort_keys=True, default=str)
        h = hashlib.sha256(raw.encode()).hexdigest()[:32]
        return f"{prefix}_{h}"
    return str(uuid.uuid4())
