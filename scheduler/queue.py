"""
Priority queue for multi-model task scheduling.
"""
import heapq
import threading
from typing import List, Tuple


class PriorityTaskQueue:
    """Thread-safe priority queue for inference tasks."""

    def __init__(self, maxsize: int = 200):
        self._heap: List[Tuple[int, int, dict]] = []
        self._counter = 0
        self._lock = threading.Lock()
        self._maxsize = maxsize

    def put(self, task: dict, priority: int = 5):
        """Add task to queue. Higher priority = processed first."""
        with self._lock:
            if len(self._heap) >= self._maxsize:
                raise QueueFullError(f"Queue full (max {self._maxsize})")
            self._counter += 1
            # Negate priority for min-heap behavior (higher priority = smaller value)
            heapq.heappush(self._heap, (-priority, self._counter, task))

    def get(self) -> dict:
        """Get highest priority task."""
        with self._lock:
            if not self._heap:
                raise QueueEmptyError("Queue is empty")
            _, _, task = heapq.heappop(self._heap)
            return task

    def peek(self) -> dict:
        """Look at highest priority task without removing."""
        with self._lock:
            if not self._heap:
                raise QueueEmptyError("Queue is empty")
            return self._heap[0][2]

    def qsize(self) -> int:
        with self._lock:
            return len(self._heap)

    def empty(self) -> bool:
        with self._lock:
            return len(self._heap) == 0

    def full(self) -> bool:
        with self._lock:
            return len(self._heap) >= self._maxsize

    def get_all(self) -> List[dict]:
        """Get all tasks sorted by priority."""
        with self._lock:
            sorted_items = sorted(self._heap, key=lambda x: x[0])
            tasks = [item[2] for item in sorted_items]
            self._heap = []
            return tasks

    def remove(self, task_id: str) -> bool:
        """Remove a specific task by ID."""
        with self._lock:
            for i, (pri, cnt, task) in enumerate(self._heap):
                if task.get("id") == task_id:
                    self._heap.pop(i)
                    heapq.heapify(self._heap)
                    return True
            return False


class QueueFullError(Exception):
    pass


class QueueEmptyError(Exception):
    pass
