"""
Redis client with enhanced task schema for multi-model support.
Supports priority queues, resource tracking, and model-aware task management.
"""
import os
import json
import redis
import time
import threading

REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))

# Shared connection pool — reused across all Redis calls in this process
_POOL = None


def _get_pool():
    """Lazy-init a connection pool shared by all workers/threads."""
    global _POOL
    if _POOL is None and REDIS_HOST:
        _POOL = redis.ConnectionPool(
            host=REDIS_HOST, port=REDIS_PORT,
            decode_responses=True,
            max_connections=20,      # enough for 4 workers + API handlers
            socket_timeout=5,
            socket_connect_timeout=3,
            retry_on_timeout=True,
        )
    return _POOL


def _get_redis():
    """Get a Redis client backed by the shared pool."""
    pool = _get_pool()
    if pool is None:
        return None
    return redis.Redis(connection_pool=pool)


r = None
REDIS_AVAILABLE = False
memory_store = {}


def wait_for_redis(max_retries=60, retry_interval=1):
    global REDIS_AVAILABLE

    if not REDIS_HOST:
        print("[Redis] REDIS_HOST not set, using memory store")
        return

    client = _get_redis()
    if client is None:
        print("[Redis] No connection pool available, using memory store")
        return

    for retry in range(max_retries):
        try:
            client.ping()
            REDIS_AVAILABLE = True
            print(f"[Redis] Connected to {REDIS_HOST}:{REDIS_PORT} (pooled)")
            return
        except Exception as e:
            if retry < max_retries - 1:
                print(f"[Redis] Waiting ({retry+1}/{max_retries}): {e}")
                time.sleep(retry_interval)
            else:
                print(f"[Redis] Not available after {max_retries} attempts, using memory store")


def redis_reconnect_loop(interval=10):
    global REDIS_AVAILABLE

    while True:
        time.sleep(interval)

        if not REDIS_HOST:
            continue

        client = _get_redis()
        if client is None:
            continue

        if REDIS_AVAILABLE:
            try:
                client.ping()
            except Exception as e:
                print(f"[Redis] Connection lost: {e}")
                REDIS_AVAILABLE = False
        else:
            try:
                client.ping()
                REDIS_AVAILABLE = True
                print(f"[Redis] Reconnected to {REDIS_HOST}:{REDIS_PORT}")
            except Exception:
                pass


# ---- Priority Queue Operations (Redis sorted-set backed) ----

def enqueue_task(task_id: str, priority: int = 5):
    """Add task to Redis priority queue (higher priority = processed first).

    Uses negated priority so higher-priority tasks sort first in the sorted set.
    Priority range: 1 (lowest) to 10 (highest/emergency).
    """
    client = _get_redis()
    if REDIS_AVAILABLE and client:
        try:
            client.zadd("priority_queue", {task_id: -priority})
            return  # Success
        except Exception as e:
            print(f"[Redis] zadd failed ({e}), using memory store for enqueue")
    memory_store[f"pq:{task_id}"] = priority


def dequeue_task() -> str:
    """Get highest priority task_id from queue (atomic pop).

    Uses ZPOPMIN which atomically removes and returns the item
    with the lowest score (highest priority, since we negate).
    Falls back to BZPOPMIN-style polling if Redis is unavailable.
    """
    client = _get_redis()
    if REDIS_AVAILABLE and client:
        try:
            # ZPOPMIN atomically pops the lowest-score item — no race condition
            result = client.zpopmin("priority_queue", count=1)
            if result:
                # result is [(member, score), ...]
                task_id = result[0][0]
                return task_id
        except Exception:
            pass

    # ALSO check fallback memory store — enqueue_task may have silently
    # failed to write to Redis but succeeded in memory_store
    if memory_store:
        # Only check keys prefixed with "pq:" (priority queue entries)
        pq_keys = [k for k in memory_store if k.startswith("pq:")]
        if pq_keys:
            best_key = max(pq_keys, key=lambda k: memory_store[k])
            memory_store.pop(best_key, None)
            return best_key.replace("pq:", "")
    return None


def get_queue_length() -> int:
    client = _get_redis()
    if REDIS_AVAILABLE and client:
        try:
            return client.zcard("priority_queue")
        except Exception:
            pass
    return len(memory_store)


def clear_queue():
    """Remove all queued tasks (not in-progress ones)."""
    client = _get_redis()
    if REDIS_AVAILABLE and client:
        try:
            client.delete("priority_queue")
        except Exception:
            pass
    memory_store.clear()


# ---- Task Storage Operations ----

def save_task(task: dict):
    """Save task with enhanced schema to Redis."""
    key = f"inference:{task['id']}"
    value = json.dumps(task)
    client = _get_redis()
    if REDIS_AVAILABLE and client:
        try:
            client.set(key, value)
        except Exception:
            memory_store[key] = value
    else:
        memory_store[key] = value


def get_task(task_id: str) -> str:
    """Get raw task JSON from Redis."""
    key = f"inference:{task_id}"
    client = _get_redis()
    if REDIS_AVAILABLE and client:
        try:
            return client.get(key)
        except Exception:
            return memory_store.get(key)
    else:
        return memory_store.get(key)


def get_tasks(limit=50):
    """Get recent tasks using SCAN (non-blocking, production-safe)."""
    client = _get_redis()
    keys = []
    if REDIS_AVAILABLE and client:
        try:
            cursor = 0
            while True:
                cursor, batch = client.scan(cursor, match="inference:*", count=100)
                keys.extend(batch)
                if cursor == 0:
                    break
        except Exception:
            keys = [k for k in memory_store.keys() if k.startswith("inference:")]
    else:
        keys = [k for k in memory_store.keys() if k.startswith("inference:")]

    result = []
    for key in keys:
        if key.startswith("inference:result:"):
            continue
        data = redis_get(key)
        if data:
            try:
                result.append(json.loads(data))
            except json.JSONDecodeError:
                pass

    sorted_result = sorted(result, key=lambda t: str(t.get("start_time", "")), reverse=True)
    return sorted_result[:limit]


def delete_task(task_id: str):
    key = f"inference:{task_id}"
    client = _get_redis()
    if REDIS_AVAILABLE and client:
        try:
            client.delete(key)
        except Exception:
            memory_store.pop(key, None)
    else:
        memory_store.pop(key, None)


# ---- Generic Redis Operations ----

def redis_set(key: str, value: str):
    client = _get_redis()
    if REDIS_AVAILABLE and client:
        try:
            client.set(key, value)
        except Exception:
            memory_store[key] = value
    else:
        memory_store[key] = value


def redis_get(key: str) -> str:
    client = _get_redis()
    if REDIS_AVAILABLE and client:
        try:
            return client.get(key)
        except Exception:
            return memory_store.get(key)
    else:
        return memory_store.get(key)


def redis_keys(pattern: str):
    """Get keys matching pattern. Uses SCAN in production, fallback for memory store."""
    client = _get_redis()
    if REDIS_AVAILABLE and client:
        try:
            # Use SCAN to avoid blocking Redis
            cursor = 0
            keys = []
            while True:
                cursor, batch = client.scan(cursor, match=pattern, count=100)
                keys.extend(batch)
                if cursor == 0:
                    break
            return keys
        except Exception:
            pass
    return [k for k in memory_store.keys() if k.startswith(pattern.replace("*", ""))]


def redis_delete(key: str):
    client = _get_redis()
    if REDIS_AVAILABLE and client:
        try:
            client.delete(key)
        except Exception:
            memory_store.pop(key, None)
    else:
        memory_store.pop(key, None)


# ---- Model Profile / Node Registry ----

def get_node_registry() -> dict:
    """Get known nodes with their roles and resource availability."""
    data = redis_get("cluster:node_registry")
    if data:
        return json.loads(data)
    return {}


def update_node_status(node_name: str, status: dict):
    registry = get_node_registry()
    registry[node_name] = {
        **registry.get(node_name, {}),
        **status,
        "last_updated": time.time()
    }
    redis_set("cluster:node_registry", json.dumps(registry))


# Initialize
wait_for_redis()

if REDIS_HOST:
    reconnect_thread = threading.Thread(target=redis_reconnect_loop, daemon=True)
    reconnect_thread.start()
