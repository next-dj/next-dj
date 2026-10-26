"""The Lua scripts the Redis broker runs and their handles bound to one client."""

from collections.abc import Awaitable
from dataclasses import dataclass
from typing import Final, Protocol, Self


class Script[R](Protocol):
    """A registered script, called with its keys and arguments."""

    def __call__(self, *, keys: list[str], args: list[str]) -> R:
        """Run the script, through its SHA once Redis has cached it."""


class ScriptClient[R](Protocol):
    """A sync or async Redis client that registers scripts for later calls."""

    def register_script(self, script: str) -> Script[R]:
        """Return a handle that runs `script`."""


PUBLISH: Final = """
redis.call('HINCRBY', KEYS[1], 'seq', 1)
local state = redis.call('HMGET', KEYS[1], 'seq', 'epoch')
local epoch = state[2]
local size = tonumber(ARGV[4])
if state[1] == '1' or not epoch then
  epoch = ARGV[3]
  redis.call('HSET', KEYS[1], 'epoch', epoch)
  if size > 0 then
    redis.call('DEL', KEYS[2])
  end
end
local payload = '{"seq":' .. state[1] .. ',"epoch":"' .. epoch .. '",'
  .. string.sub(ARGV[2], 2)
if size > 0 then
  redis.call('LPUSH', KEYS[2], payload)
  redis.call('LTRIM', KEYS[2], 0, size - 1)
  redis.call('PEXPIRE', KEYS[2], ARGV[5])
end
redis.call('PEXPIRE', KEYS[1], ARGV[5])
redis.call('PUBLISH', ARGV[1], payload)
return {state[1], epoch}
"""

_CLOCK = """
local clock = redis.call('TIME')
local now = tonumber(clock[1]) * 1000 + math.floor(tonumber(clock[2]) / 1000)
"""
_PRUNE = (
    _CLOCK
    + """
local SEP = string.char(0)
local BATCH = 1000
local changed = redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now) > 0
local lapsed = redis.call('ZRANGEBYSCORE', KEYS[4], '-inf', now)
if #lapsed > 0 then
  redis.call('ZREMRANGEBYSCORE', KEYS[4], '-inf', now)
  for first = 1, #lapsed, BATCH do
    local last = math.min(first + BATCH - 1, #lapsed)
    local payloads = redis.call('HMGET', KEYS[2], unpack(lapsed, first, last))
    local held = {}
    for index = 1, #payloads do
      if payloads[index] then
        held[#held + 1] = payloads[index] .. SEP .. lapsed[first + index - 1]
      end
    end
    redis.call('HDEL', KEYS[2], unpack(lapsed, first, last))
    if #held > 0 then
      redis.call('ZREM', KEYS[6], unpack(held))
    end
  end
  local gone = redis.call('ZRANGEBYSCORE', KEYS[5], '-inf', now)
  if #gone > 0 then
    redis.call('ZREMRANGEBYSCORE', KEYS[5], '-inf', now)
    for first = 1, #gone, BATCH do
      local last = math.min(first + BATCH - 1, #gone)
      redis.call('ZREM', KEYS[8], unpack(gone, first, last))
    end
    changed = true
  end
end
"""
)
_SETTLE = """
local function settle(window)
  if changed then
    redis.call('HINCRBY', KEYS[7], 'version', 1)
  end
  local state = redis.call('HMGET', KEYS[7], 'version', 'published')
  if state[1] and state[1] ~= state[2]
      and redis.call('SET', KEYS[3], '1', 'NX', 'PX', window) then
    return 1
  end
  return 0
end
"""
_KEEP = """
local function keep(ttl)
  for _, group in ipairs({{1}, {4, 2, 5, 6, 8}, {7}}) do
    local left = redis.call('PTTL', KEYS[group[1]])
    if left ~= -2 and left < ttl then
      for _, index in ipairs(group) do
        redis.call('PEXPIRE', KEYS[index], ttl * 2)
      end
    end
  end
end
"""
_LATEST = """
local function latest(payload)
  local holders = redis.call(
    'ZRANGEBYLEX', KEYS[6], '[' .. payload .. SEP, '(' .. payload .. string.char(1))
  local count, best = 0, 0
  for _, holder in ipairs(holders) do
    local connection = string.sub(holder, #payload + 2)
    local deadline = redis.call('ZSCORE', KEYS[4], connection)
    if deadline then
      count = count + 1
      best = math.max(best, tonumber(deadline))
    else
      redis.call('ZREM', KEYS[6], holder)
      redis.call('HDEL', KEYS[2], connection)
    end
  end
  return count, best
end
"""
_RESCORE = """
local function rescore(connection, payload, deadline)
  local held = redis.call('ZSCORE', KEYS[5], payload)
  if not held or tonumber(held) <= deadline then
    redis.call('ZADD', KEYS[4], deadline, connection)
    redis.call('ZADD', KEYS[5], deadline, payload)
    return
  end
  local before = redis.call('ZSCORE', KEYS[4], connection)
  redis.call('ZADD', KEYS[4], deadline, connection)
  if before and tonumber(before) >= tonumber(held) then
    local _, best = latest(payload)
    redis.call('ZADD', KEYS[5], best, payload)
  end
end
"""
_DETACH = """
local function detach(connection, payload)
  redis.call('ZREM', KEYS[4], connection)
  redis.call('HDEL', KEYS[2], connection)
  redis.call('ZREM', KEYS[6], payload .. SEP .. connection)
  local count, best = latest(payload)
  if count == 0 then
    redis.call('ZREM', KEYS[5], payload)
    redis.call('ZREM', KEYS[8], payload)
    return true
  end
  redis.call('ZADD', KEYS[5], best, payload)
  return false
end
"""
JOIN: Final = (
    _PRUNE
    + _SETTLE
    + _KEEP
    + _LATEST
    + _RESCORE
    + _DETACH
    + """
local ttl = tonumber(ARGV[2])
local deadline = now + ttl
local previous = redis.call('HGET', KEYS[2], ARGV[1])
if ARGV[3] == '' then
  if previous then
    detach(ARGV[1], previous)
  end
  if redis.call('ZADD', KEYS[1], deadline, ARGV[1]) == 1 then
    changed = true
  end
else
  redis.call('ZREM', KEYS[1], ARGV[1])
  if previous ~= ARGV[3] then
    if previous then
      detach(ARGV[1], previous)
    end
    redis.call('HSET', KEYS[2], ARGV[1], ARGV[3])
    redis.call('ZADD', KEYS[6], 0, ARGV[3] .. SEP .. ARGV[1])
    redis.call('ZADD', KEYS[8], 'NX', now, ARGV[3])
    changed = true
  end
  rescore(ARGV[1], ARGV[3], deadline)
end
local claimed = settle(ARGV[4])
keep(ttl)
return claimed
"""
)
TOUCH: Final = (
    _PRUNE
    + _SETTLE
    + _KEEP
    + _LATEST
    + _RESCORE
    + """
local ttl = tonumber(ARGV[2])
local deadline = now + ttl
local payload = redis.call('HGET', KEYS[2], ARGV[1])
local present = 1
if payload then
  rescore(ARGV[1], payload, deadline)
elseif redis.call('ZSCORE', KEYS[1], ARGV[1]) then
  redis.call('ZADD', KEYS[1], 'XX', deadline, ARGV[1])
else
  present = 0
end
local claimed = settle(ARGV[3])
keep(ttl)
return {present, claimed}
"""
)
LEAVE: Final = (
    _PRUNE
    + _SETTLE
    + _LATEST
    + _DETACH
    + """
local payload = redis.call('HGET', KEYS[2], ARGV[1])
if payload then
  if detach(ARGV[1], payload) then
    changed = true
  end
elseif redis.call('ZREM', KEYS[1], ARGV[1]) == 1 then
  changed = true
end
local claimed = settle(ARGV[2])
if changed and redis.call('PTTL', KEYS[7]) == -1 then
  redis.call('PEXPIRE', KEYS[7], ARGV[2] * 2)
end
if claimed == 0 and redis.call('EXISTS', KEYS[1], KEYS[5]) == 0 then
  local state = redis.call('HMGET', KEYS[7], 'version', 'published')
  if state[1] and state[1] ~= state[2] then
    return {0, redis.call('PTTL', KEYS[3])}
  end
end
return {claimed, 0}
"""
)
_LIST = (
    _CLOCK
    + """
local live = '(' .. now
local attendees = redis.call('ZCOUNT', KEYS[2], live, '+inf')
local result = {redis.call('ZCOUNT', KEYS[1], live, '+inf') + attendees}
local size = tonumber(ARGV[1])
local lapsed = redis.call('ZCARD', KEYS[2]) > attendees
if size > 0 and not lapsed then
  for _, payload in ipairs(redis.call('ZRANGE', KEYS[3], 0, size - 1)) do
    result[#result + 1] = payload
  end
end
local start = 0
while lapsed and #result <= size do
  local chunk = redis.call('ZRANGE', KEYS[3], start, start + 255)
  if #chunk == 0 then
    break
  end
  for _, payload in ipairs(chunk) do
    local deadline = redis.call('ZSCORE', KEYS[2], payload)
    if deadline and tonumber(deadline) > now then
      result[#result + 1] = payload
      if #result > size then
        break
      end
    end
  end
  start = start + 256
end
"""
)
SNAPSHOT: Final = _LIST + "return result\n"
ANNOUNCE: Final = (
    """
local state = redis.call('HMGET', KEYS[4], 'version', 'published')
if state[1] and state[1] == state[2] then
  return false
end
"""
    + _LIST
    + """
if state[1] then
  redis.call('HSET', KEYS[4], 'published', state[1])
end
local detail = '{"count":' .. result[1]
if #result > 1 then
  detail = detail .. ',"members":[' .. table.concat(result, ',', 2) .. ']'
end
redis.call('PUBLISH', ARGV[2], ARGV[3] .. detail .. '}' .. ARGV[4])
return result
"""
)


@dataclass(frozen=True, slots=True)
class Scripts[R]:
    """Every script of the broker bound to one client, sync or async alike."""

    publish: Script[R]
    join: Script[R]
    touch: Script[R]
    leave: Script[R]
    snapshot: Script[R]
    announce: Script[R]

    @classmethod
    def bind(cls, client: ScriptClient[R]) -> Self:
        """Register each script on `client`, which loads it on first call."""
        return cls(
            client.register_script(PUBLISH),
            client.register_script(JOIN),
            client.register_script(TOUCH),
            client.register_script(LEAVE),
            client.register_script(SNAPSHOT),
            client.register_script(ANNOUNCE),
        )


type SyncScripts = Scripts[object]
type AsyncScripts = Scripts[Awaitable[object]]


__all__ = [
    "ANNOUNCE",
    "JOIN",
    "LEAVE",
    "PUBLISH",
    "SNAPSHOT",
    "TOUCH",
    "AsyncScripts",
    "Script",
    "ScriptClient",
    "Scripts",
    "SyncScripts",
]
