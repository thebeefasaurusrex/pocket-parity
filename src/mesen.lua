-- The host supplies a private mailbox directory and a console identifier.
local directory = ROMLAB.directory
local memory = emu.memType[ROMLAB.memory]
local cpu = emu.cpuType[ROMLAB.cpu]
local button = nil
local remaining = 0
local stepped = false
local states = {}

local function retry(action)
    local deadline = os.clock() + 10
    repeat
        local success, reason = action()
        if success then return success end
        if os.clock() > deadline then error(reason) end
    until false
end

local function reply(kind, value)
    local temporary = directory .. "/reply.tmp"
    local output = retry(function() return io.open(temporary, "wb") end)
    output:write(kind, "\n", value or "")
    output:close()
    retry(function() return os.rename(temporary, directory .. "/reply") end)
end

local function request()
    local path = directory .. "/request"
    local deadline = os.clock() + 12
    repeat
        local input = io.open(path, "rb")
        if input then
            local body = input:read("*a")
            input:close()
            retry(function() return os.remove(path) end)
            return body:match("^(%S+)%s*(.*)$")
        end
    until os.clock() > deadline
    error("PocketParity host stopped responding")
end

emu.addEventCallback(function(eventCpu)
    if eventCpu ~= cpu then return end
    if remaining > 0 then
        remaining = remaining - 1
    end
end, emu.eventType.endFrame)

emu.addEventCallback(function(eventCpu)
    if eventCpu ~= cpu then return end
    local input = emu.getInput(0)
    for key, value in pairs(input) do
        if type(value) == "boolean" then input[key] = key == button end
    end
    emu.setInput(input, 0)
end, emu.eventType.inputPolled)

local function serve()
    if remaining > 0 then return end
    if stepped then
        stepped = false
        reply("OK")
    end
    while true do
        local op, arg = request()
        if op == "step" then
            local frames, nextButton = arg:match("^(%d+)%s*(.*)$")
            if nextButton ~= "" then
                assert(emu.getInput(0)[nextButton] ~= nil, "Unknown controller button " .. nextButton)
            end
            button = nextButton ~= "" and nextButton or nil
            if ROMLAB.cpu == "gameboy" then
                local input = emu.getInput(0)
                for key, value in pairs(input) do
                    if type(value) == "boolean" then input[key] = key == button end
                end
                emu.setInput(input, 0)
            end
            remaining = tonumber(frames)
            stepped = true
            return
        elseif op == "read" then
            local region, start, size = arg:match("^(%w+) (%d+) (%d+)$")
            local regionMemory = region == "cpu" and memory or emu.memType[region]
            assert(regionMemory ~= nil, "Unknown memory region " .. region)
            local bytes = {}
            for index = 0, tonumber(size) - 1 do
                bytes[index + 1] = string.char(emu.read(tonumber(start) + index, regionMemory))
            end
            reply("OK", table.concat(bytes))
        elseif op == "snap" then
            reply("OK", emu.takeScreenshot())
        elseif op == "save" then
            states[arg] = emu.createSavestate()
            reply("OK")
        elseif op == "load" then
            local state = assert(states[arg], "Unknown state " .. arg)
            emu.loadSavestate(state)
            reply("OK")
        elseif op == "exit" then
            reply("OK")
            emu.exit(0)
            return
        else
            error("Unknown request " .. tostring(op))
        end
    end
end

emu.addMemoryCallback(function()
    local ok, message = pcall(serve)
    if not ok then
        reply("ERR", tostring(message))
        emu.exit(2)
    end
end, emu.callbackType.exec, 0, ROMLAB.lastAddress, cpu, memory)
