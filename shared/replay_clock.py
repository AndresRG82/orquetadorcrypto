"""Controlled clock for deterministic replay.

In normal mode delegates to `datetime.now(timezone.utc)` and `time.time()`.
In replay mode uses a simulated time based on stream data timestamps.

Usage:
  from shared.replay_clock import ReplayClock
  clock = ReplayClock()
  clock.now()  # returns datetime (controlled in replay mode)
  clock.time()  # returns float epoch (controlled in replay mode)
"""

import time as _time
from datetime import datetime, timezone


class ReplayClock:
    def __init__(self):
        self._replay_mode = False
        self._current_time: float = 0.0

    def enable_replay(self, initial_time: float):
        self._replay_mode = True
        self._current_time = initial_time

    def advance(self, seconds: float):
        if self._replay_mode:
            self._current_time += seconds

    def set_time(self, epoch: float):
        if self._replay_mode:
            self._current_time = epoch

    def now(self) -> datetime:
        if self._replay_mode:
            return datetime.fromtimestamp(self._current_time, tz=timezone.utc)
        return datetime.now(timezone.utc)

    def time(self) -> float:
        if self._replay_mode:
            return self._current_time
        return _time.time()
