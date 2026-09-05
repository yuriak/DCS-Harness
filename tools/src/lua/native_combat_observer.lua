-- Harness-owned bounded redundant observer for combat-critical DCS events.
-- This file is sent through the existing DCS-gRPC Eval path; it is not loaded
-- from or installed into the player's Saved Games directory.

local VERSION = 1
local CAPACITY = 512
local GLOBAL_NAME = "DCS_HARNESS_COMBAT_OBSERVER_V1"

local function safe_index(object, key)
  if object == nil then return nil end
  local ok, value = pcall(function() return object[key] end)
  if ok then return value end
  return nil
end

local function safe_call(object, method_name)
  local method = safe_index(object, method_name)
  if type(method) ~= "function" then return nil end
  local ok, value = pcall(method, object)
  if not ok then return nil end
  local value_type = type(value)
  if value_type == "string" or value_type == "number" or value_type == "boolean" then
    return value
  end
  return nil
end

local function safe_object_call(object, method_name)
  local method = safe_index(object, method_name)
  if type(method) ~= "function" then return nil end
  local ok, value = pcall(method, object)
  if ok then return value end
  return nil
end

local function coalition_name(value)
  if value == 0 then return "neutral" end
  if value == 1 then return "red" end
  if value == 2 then return "blue" end
  return nil
end

local function object_kind(object)
  local category = safe_call(object, "getCategory")
  if Object and Object.Category then
    if category == Object.Category.UNIT then return "unit" end
    if category == Object.Category.WEAPON then return "weapon" end
    if category == Object.Category.STATIC then return "static" end
    if category == Object.Category.SCENERY then return "scenery" end
    if category == Object.Category.BASE then return "airbase" end
  end
  return "unknown"
end

local function extract_entity(object)
  if object == nil then return nil end
  local kind = object_kind(object)
  local result = {
    kind = kind,
    exists = safe_call(object, "isExist"),
    object_id = safe_call(object, "getID"),
    object_name = safe_call(object, "getName"),
    type = safe_call(object, "getTypeName"),
    coalition = coalition_name(safe_call(object, "getCoalition")),
  }
  if kind == "unit" then
    result.unit_id = result.object_id
    result.unit_name = result.object_name
    result.player_name = safe_call(object, "getPlayerName")
    local group = safe_object_call(object, "getGroup")
    if group ~= nil then
      result.group_id = safe_call(group, "getID")
      result.group_name = safe_call(group, "getName")
    end
  end
  return result
end

local function extract_weapon(event)
  local weapon = safe_index(event, "weapon")
  local event_weapon_name = safe_index(event, "weapon_name")
  if weapon == nil and type(event_weapon_name) ~= "string" then return nil end
  local runtime_id = safe_index(weapon, "id_")
  if type(runtime_id) ~= "number" and type(runtime_id) ~= "string" then
    runtime_id = safe_call(weapon, "getID")
  end
  return {
    runtime_id = runtime_id,
    type = safe_call(weapon, "getTypeName"),
    event_weapon_name = type(event_weapon_name) == "string" and event_weapon_name or nil,
  }
end

local event_names = {}
local function register(symbol, name)
  local id = world and world.event and world.event[symbol]
  if type(id) == "number" then event_names[id] = name end
end

register("S_EVENT_SHOT", "shot")
register("S_EVENT_HIT", "hit")
register("S_EVENT_KILL", "kill")
register("S_EVENT_DEAD", "dead")
register("S_EVENT_UNIT_LOST", "unit_lost")
register("S_EVENT_CRASH", "crash")
register("S_EVENT_EJECTION", "ejection")
register("S_EVENT_SHOOTING_START", "shooting_start")
register("S_EVENT_SHOOTING_END", "shooting_end")

local state = rawget(_G, GLOBAL_NAME)
if type(state) ~= "table" or state.version ~= VERSION then
  if type(state) == "table" and state.handler and world and world.removeEventHandler then
    pcall(world.removeEventHandler, state.handler)
  end
  state = {
    version = VERSION,
    capacity = CAPACITY,
    next_sequence = 1,
    oldest_sequence = 1,
    overwritten = 0,
    extraction_errors = 0,
    events = {},
  }
  rawset(_G, GLOBAL_NAME, state)
end

local function append_event(event_type, event)
  local sequence = state.next_sequence
  local mission_time = safe_index(event, "time")
  if type(mission_time) ~= "number" then
    mission_time = timer and timer.getTime and timer.getTime() or 0
  end
  local record = {
    native_sequence = sequence,
    mission_time = mission_time,
    event_type = event_type,
    initiator = extract_entity(safe_index(event, "initiator")),
    target = extract_entity(safe_index(event, "target")),
    weapon = extract_weapon(event),
  }
  state.events[((sequence - 1) % state.capacity) + 1] = record
  state.next_sequence = sequence + 1
  local next_oldest = math.max(1, state.next_sequence - state.capacity)
  if next_oldest > state.oldest_sequence then
    state.overwritten = state.overwritten + (next_oldest - state.oldest_sequence)
    state.oldest_sequence = next_oldest
  end
end

if state.handler == nil then
  local handler = {}
  function handler:onEvent(event)
    local event_type = event and event_names[event.id]
    if event_type == nil then return end
    local ok = pcall(append_event, event_type, event)
    if not ok then state.extraction_errors = state.extraction_errors + 1 end
  end
  state.handler = handler
  world.addEventHandler(handler)
end

function DCS_HARNESS_COMBAT_POLL(after_sequence, limit)
  after_sequence = math.max(0, math.floor(tonumber(after_sequence) or 0))
  limit = math.max(1, math.min(200, math.floor(tonumber(limit) or 100)))
  local first = math.max(after_sequence + 1, state.oldest_sequence)
  local latest = state.next_sequence - 1
  local result = {}
  local last = math.min(latest, first + limit - 1)
  for sequence = first, last do
    local record = state.events[((sequence - 1) % state.capacity) + 1]
    if record and record.native_sequence == sequence then
      result[#result + 1] = record
    end
  end
  return {
    available = true,
    version = state.version,
    capacity = state.capacity,
    oldest_sequence = state.oldest_sequence,
    latest_sequence = latest,
    overwritten = state.overwritten,
    extraction_errors = state.extraction_errors,
    gap = after_sequence < state.oldest_sequence - 1,
    has_more = last < latest,
    events = result,
  }
end
