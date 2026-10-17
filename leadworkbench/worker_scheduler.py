"""Fair work-item turns, independent of worker locks and source implementations."""
import asyncio
from collections import deque
import logging


log = logging.getLogger(__name__)
# Seconds idle() waits for running lanes before the worker decides it is busy.
IDLE_GRACE_SECONDS = 0.2


class WorkScheduler:
    """Rotate active runs, giving social work a slot after every market item.

    Refresh runs use the same queue as market runs. New and resumed runs join
    the tail; paused, cancelled and completed runs leave at the next turn.
    """

    def __init__(self):
        self._runs = deque()

    async def turn(self, store, process_run_item, process_social_item):
        active = [run["id"] for run in store.active_runs()]
        active_ids = set(active)
        self._runs = deque(run_id for run_id in self._runs if run_id in active_ids)
        queued = set(self._runs)
        self._runs.extend(run_id for run_id in active if run_id not in queued)
        if self._runs:
            run_id = self._runs.popleft()
            # Advance before awaiting, so an interrupted turn cannot monopolize
            # the queue on the next iteration.
            self._runs.append(run_id)
            await process_run_item(store, run_id)
        await process_social_item(store)


class ParallelWorkScheduler:
    """Keep at most one item running in each of three independent lanes.

    Turns only refill idle lanes; source tasks survive subsequent turns.
    Each market lane owns its run rotation and skips runs with no eligible
    item. The owner must await aclose(), or observe idle() == True, before
    releasing its operation lock.
    """

    MARKET_STAGES = {
        "maps": ("planning", "discovery"),
        "website": ("refresh",),
    }

    def __init__(self):
        self._runs = {lane: deque() for lane in self.MARKET_STAGES}
        self._tasks = {}
        self._closed = False

    def _reap(self):
        for lane, task in list(self._tasks.items()):
            if not task.done():
                continue
            del self._tasks[lane]
            try:
                task.result()
            except asyncio.CancelledError:
                if not self._closed:
                    log.warning("Worker %s lane cancelled", lane)
            except Exception:
                log.exception("Worker %s lane failed", lane)

    async def _market_item(self, store, process_run_item, lane):
        # Bound empty probes to one pass. Rotate before awaiting so failures
        # and interrupted items cannot repeatedly jump ahead of other runs.
        for _ in range(len(self._runs[lane])):
            queue = self._runs[lane]
            if not queue:
                break
            run_id = queue.popleft()
            queue.append(run_id)
            if await process_run_item(store, run_id, stages=self.MARKET_STAGES[lane]):
                return True
        return False

    async def turn(self, store, process_run_item, process_social_item):
        if self._closed:
            raise RuntimeError("Worker scheduler is closed")
        self._reap()
        active = [run["id"] for run in store.active_runs()]
        active_ids = set(active)
        for lane in self.MARKET_STAGES:
            queue = deque(run_id for run_id in self._runs[lane] if run_id in active_ids)
            queued = set(queue)
            queue.extend(run_id for run_id in active if run_id not in queued)
            self._runs[lane] = queue
            if lane not in self._tasks and queue:
                self._tasks[lane] = asyncio.create_task(
                    self._market_item(store, process_run_item, lane),
                    name=f"worker-{lane}")
        if "social" not in self._tasks:
            self._tasks["social"] = asyncio.create_task(
                process_social_item(store), name="worker-social")
        # Give newly launched tasks an event-loop opportunity without waiting
        # for any source to finish or cancelling tasks at the tick boundary.
        await asyncio.wait(self._tasks.values(), timeout=0)

    async def idle(self, grace=None):
        """True when no lane task remains; waits up to `grace` seconds for running lanes."""
        if self._closed:
            return True
        grace = IDLE_GRACE_SECONDS if grace is None else grace
        pending = [task for task in self._tasks.values() if not task.done()]
        if pending:  # asyncio.wait rejects an empty collection.
            await asyncio.wait(pending, timeout=grace)
        self._reap()
        return not self._tasks

    async def aclose(self):
        self._closed = True
        for task in self._tasks.values():
            if not task.done():
                task.cancel()
        await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        self._reap()
