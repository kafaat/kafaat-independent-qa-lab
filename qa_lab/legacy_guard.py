"""Bounded in-process circuit for the authenticated read-only legacy catalog probe.

Advisory state is local to one API process; never retries the external command.
"""
import threading
import time
from .reliability_policy import Circuit, decide


class LegacyProbeGuard:
    def __init__(self, threshold=3, cooldown=30, clock=None):
        self._state = Circuit()
        self._lock = threading.Lock()
        self._clock = clock or time.monotonic
        self._threshold = threshold
        self._cooldown = cooldown

    def call(self, probe):
        with self._lock:
            now = self._clock()
            _, admitted = decide(self._state, now=now, threshold=self._threshold, cooldown=self._cooldown)
            if not admitted:
                raise RuntimeError('legacy_circuit_open')
            try:
                result = probe()
            except (OSError, ValueError):
                self._state, _ = decide(self._state, now=self._clock(), outcome='failure',
                                        threshold=self._threshold, cooldown=self._cooldown)
                raise
            else:
                self._state, _ = decide(self._state, now=self._clock(), outcome='success',
                                        threshold=self._threshold, cooldown=self._cooldown)
                return result
