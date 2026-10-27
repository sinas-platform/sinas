// Redis memory on the System page (GET /system/health `redis`). Pure
// functions (tested in tests/redis-memory.test.mjs).
//
// Sinas keeps state in Redis (queues, locks, stream relays), not a cache:
// with maxmemory-policy noeviction a full Redis refuses writes, so its
// memory against maxmemory is the number to watch.

export interface RedisMemory {
  used_bytes: number;
  maxmemory_bytes: number | null; // null: no limit
  percent: number | null;
  policy: string | null;
  evicted_keys: number;
  oom_errors: number | null; // writes refused for lack of memory; null before Redis 6.2
}

/** 1536 -> "1.5 KB", 268435456 -> "256 MB". */
export function formatBytes(bytes: number): string {
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  const digits = unit === 0 || value >= 100 || Number.isInteger(value) ? 0 : 1;
  return `${value.toFixed(digits)} ${units[unit]}`;
}

/** "48.2 MB of 256 MB", or "48.2 MB (no limit)". */
export function redisMemoryLabel(redis: RedisMemory): string {
  const used = formatBytes(redis.used_bytes);
  return redis.maxmemory_bytes ? `${used} of ${formatBytes(redis.maxmemory_bytes)}` : `${used} (no limit)`;
}

/** What a full Redis does under its policy, for the policy line's tooltip. */
export function redisPolicyNote(policy: string | null): string {
  if (!policy) return '';
  if (policy === 'noeviction') return 'When full, Redis refuses writes: nothing stored is evicted, but requests and jobs that need to write fail until it has memory again.';
  return 'When full, Redis evicts keys: queued jobs, locks and the dead letter queue can be lost. Use noeviction.';
}
