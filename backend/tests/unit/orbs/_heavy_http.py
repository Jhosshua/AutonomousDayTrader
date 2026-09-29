"""A replay transport that also parses a big JSON body on every relay answer (like the scanner parsing large
SIP trade/quote pages): json.loads holds the GIL for its whole C call, so scan threads stall any other
thread of their process. Built inside the decision process by FacadeProxy."""
import json
import time

_BIG = json.dumps([{"t": "2031-03-04T09:30:00.000000Z", "p": 100.0 + i % 97, "s": 100, "c": ["@"]}
                   for i in range(250_000)])


class BurnTransport:
    def __init__(self, inner, burn_s):
        self.inner, self.burn_s = inner, burn_s
        self.misses = inner.misses

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def __call__(self, req, timeout=None):
        end = time.perf_counter() + self.burn_s
        while time.perf_counter() < end:
            json.loads(_BIG)                # one C call, GIL held throughout (~100+ ms)
        return self.inner(req, timeout)


def make(cache_root, day, burn_s):
    import parity_common as common
    common.CACHE_ROOT = cache_root
    return BurnTransport(common.Transport(day, "replay"), burn_s)


class Stall:
    """Every relay answer takes minutes (a hung upstream)."""
    misses = []

    def __call__(self, req, timeout=None):
        time.sleep(300)


def make_stall():
    return Stall()


def make_stall_at_start():
    """The decision process never becomes ready (its construction hangs)."""
    time.sleep(300)
