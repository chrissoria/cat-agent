"""Agent-agnostic plumbing: bounded-concurrency execution of sealed calls.

classify() is synchronous (matching the rest of the CatLLM ecosystem); the
SDKs are asyncio-native. This module owns that seam: build one coroutine per
row, run them all under a semaphore in a single event loop.
"""

import asyncio


def gather_bounded(coro_fns, max_workers: int = 4):
    """Run zero-arg coroutine factories with at most `max_workers` in flight.

    Returns results in input order. Factory exceptions are captured and
    returned in place of results (callers decide how to record the failure) —
    one bad row must never abort the batch.
    """

    async def _run():
        sem = asyncio.Semaphore(max(1, int(max_workers)))

        async def _bounded(fn):
            async with sem:
                try:
                    return await fn()
                except Exception as e:
                    return e

        return await asyncio.gather(*[_bounded(fn) for fn in coro_fns])

    return asyncio.run(_run())
