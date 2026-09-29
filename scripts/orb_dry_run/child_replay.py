"""Replay injection for ORB's decision CHILD process (backend/app/core/orb_facade_proc.py).

FacadeProxy passes `http_factory` to the spawned child, which calls it once before building OrbsFacade. The
dry run hands it a ChildReplay (picklable): in the child it blocks every socket, points the decision shim's
unpinned clock at the dry run's simulated time (the parent writes it to a small shared file every step) and
returns store.DryRunTransport, so every relay request of the child is answered from the recorded cache."""
from __future__ import annotations

import mmap
import os
import socket
import struct


class ChildReplay:
    def __init__(self, day: str, clock_path: str, latency_s: float = 0.0):
        self.day, self.clock_path, self.latency_s = day, clock_path, latency_s

    def __call__(self):
        def refuse(*a, **k):
            raise PermissionError("dry run child: network access is forbidden")
        socket.socket.connect = refuse
        socket.socket.connect_ex = refuse
        socket.create_connection = refuse
        f = open(self.clock_path, "r+b")
        mm = mmap.mmap(f.fileno(), 8)

        def now_ns():
            return struct.unpack("<q", mm[:8])[0]
        from datetime import datetime, timezone
        from zoneinfo import ZoneInfo
        from backend.app.strategies.orbs import shim
        et = ZoneInfo("America/New_York")

        def sim_now_et():
            pinned = shim._NOW_OVERRIDE.get()
            return pinned if pinned is not None else datetime.fromtimestamp(now_ns() / 1e9, timezone.utc).astimezone(et)
        shim.now_et = sim_now_et
        import store
        return store.DryRunTransport(self.day, now_ns, latency_s=self.latency_s)


class ClockFile:
    """Parent side: the simulated time (ns since the epoch) in an 8-byte shared file."""

    def __init__(self, path: str):
        self.path = path
        with open(path, "wb") as f:
            f.write(b"\0" * 8)
        self._f = open(path, "r+b")
        self._mm = mmap.mmap(self._f.fileno(), 8)

    def write(self, ns: int) -> None:
        self._mm[:8] = struct.pack("<q", int(ns))
