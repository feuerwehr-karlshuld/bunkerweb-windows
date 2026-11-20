-- Unit tests of windows/overlay/src/bw/lua/bunkerweb/winffi.lua with a fake ffi (run by test-port.py through
-- lupa/LuaJIT or by `luajit windows/build/test-winffi.lua <overlay lua dir>`). Returns the list of failures.

local overlay = ...
package.path = overlay .. "/?.lua;" .. package.path
local winffi = require "bunkerweb.winffi"

local failures = {}
local function check(name, condition, detail)
	if not condition then
		failures[#failures + 1] = name .. (detail and (": " .. tostring(detail)) or "")
	end
end

local function fake_ffi()
	local state = { af = {}, env = {}, cdefs = 0, loads = {}, envp = { "A=1" } }
	local real_c = setmetatable({}, {
		__index = function(_, name)
			if name == "strlen" then
				return function(s)
					return #s
				end
			end
			error("cannot resolve symbol '" .. name .. "'")
		end,
	})
	local ws2 = {
		inet_pton = function(af)
			state.af[#state.af + 1] = af
			return 1
		end,
		inet_ntop = function(af)
			state.af[#state.af + 1] = af
			return "ip"
		end,
		ntohl = function(x)
			return x + 1
		end,
	}
	local ucrt = {
		_putenv_s = function(name, value)
			state.env[name] = value
			return 0
		end,
		__p__environ = function()
			return { [0] = state.envp }
		end,
		fabs_like = function()
			return "ucrt"
		end,
		_open = function(path, oflag, pmode)
			state.open = { path = path, oflag = oflag, pmode = pmode }
			return 7
		end,
		_write = function(fd, buf, count)
			state.write = { fd = fd, buf = buf, count = count }
			return count
		end,
		_close = function(fd)
			state.closed = fd
			return 0
		end,
		_getpid = function()
			return 4242
		end,
	}
	local real = {
		C = real_c,
		os = "Windows",
		cdef = function()
			state.cdefs = state.cdefs + 1
		end,
		load = function(name)
			state.loads[#state.loads + 1] = name
			if name == "ws2_32" then
				return setmetatable({}, { __index = function(_, k) local v = ws2[k]; if v == nil then error("no " .. k) end; return v end })
			elseif name == "ucrtbase" then
				return setmetatable({}, { __index = function(_, k) local v = ucrt[k]; if v == nil then error("no " .. k) end; return v end })
			end
			error("cannot load " .. name)
		end,
		errno = function()
			return 0
		end,
		marker = "real",
	}
	return real, state
end

do -- real ffi.C wins, other fields pass through, assignments reach the real ffi
	local real, state = fake_ffi()
	local ffi = winffi.new(real)
	check("real C symbol", ffi.C.strlen("abc") == 3)
	check("os passthrough", ffi.os == "Windows")
	check("field passthrough", ffi.marker == "real")
	check("load passthrough", ffi.load == real.load)
	ffi.bw_flag = true
	check("newindex reaches real ffi", real.bw_flag == true)
	check("no library loaded for a real symbol", #state.loads == 0, #state.loads)
end

do -- winsock: AF_INET6 of Linux is mapped, ntohl found through the ws2_32 fallback
	local real, state = fake_ffi()
	local ffi = winffi.new(real)
	ffi.C.inet_pton(10, "::1", nil)
	ffi.C.inet_pton(2, "1.2.3.4", nil)
	ffi.C.inet_pton(23, "::1", nil)
	ffi.C.inet_ntop(10, nil, nil, 46)
	check("AF_INET6 mapped", state.af[1] == 23 and state.af[2] == 2 and state.af[3] == 23 and state.af[4] == 23, table.concat(state.af, ","))
	check("fallback to ws2_32", ffi.C.ntohl(1) == 2)
	check("fallback to ucrtbase", ffi.C.fabs_like() == "ucrt")
end

do -- environment functions
	local real, state = fake_ffi()
	local ffi = winffi.new(real)
	check("setenv", ffi.C.setenv("A", "b", 1) == 0 and state.env.A == "b")
	check("unsetenv", ffi.C.unsetenv("A") == 0 and state.env.A == "")
	check("environ", ffi.C.environ == state.envp)
	state.envp = { "B=2" }
	check("environ is read on every access", ffi.C.environ == state.envp)
	ffi.C.setenv("X", "1", 1)
	ffi.C.setenv("Y", "1", 1)
	check("cdef only once per declaration", state.cdefs == 2, state.cdefs) -- _putenv_s and __p__environ
end

do -- libc emulation overrides the real functions
	local real = fake_ffi()
	local libc = {
		fopen = function()
			return "emulated"
		end,
	}
	real.C.fopen = nil
	local ffi = winffi.new(real, libc)
	check("libc override", ffi.C.fopen("x", "r") == "emulated")
end

do -- POSIX fd calls go to the UCRT's _open/_write/_close/_getpid, with Linux open flags translated
	local real, state = fake_ffi()
	local ffi = winffi.new(real)
	-- what CrowdSec's enforcement_state.lua passes: O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC, mode 0660
	local fd = ffi.C.open("/x.tmp", 524481, 432)
	check("open returns the UCRT fd", fd == 7, fd)
	-- _O_WRONLY 0x1 | _O_CREAT 0x100 | _O_EXCL 0x400 | _O_NOINHERIT 0x80 | _O_BINARY 0x8000
	check("open flags translated", state.open.oflag == 0x8581, string.format("0x%x", state.open.oflag))
	check("open mode: _S_IREAD | _S_IWRITE", state.open.pmode == 0x180, state.open.pmode)
	check("open path unchanged", state.open.path == "/x.tmp")
	ffi.C.open("/y", 0x241, 292) -- O_WRONLY | O_CREAT | O_TRUNC, mode 0444
	check("O_TRUNC kept", state.open.oflag == 0x8301, string.format("0x%x", state.open.oflag))
	check("read-only mode", state.open.pmode == 0x100, state.open.pmode)
	ffi.C.open("/z", 0x402) -- O_RDWR | O_APPEND, no mode
	check("O_APPEND mapped", state.open.oflag == 0x800a, string.format("0x%x", state.open.oflag))
	check("default mode", state.open.pmode == 0x180, state.open.pmode)
	check("write", ffi.C.write(7, "data", 4) == 4 and state.write.fd == 7 and state.write.count == 4)
	check("close", ffi.C.close(7) == 0 and state.closed == 7)
	check("getpid", ffi.C.getpid() == 4242)
end

do -- unknown symbols fail like they always did
	local real = fake_ffi()
	local ffi = winffi.new(real)
	local ok, err = pcall(function()
		return ffi.C.does_not_exist
	end)
	check("unknown symbol raises", not ok)
	check("original error kept", tostring(err):find("cannot resolve symbol 'does_not_exist'", 1, true) ~= nil, err)
end

do -- a missing fallback library is not fatal
	local real = fake_ffi()
	local original_load = real.load
	real.load = function(name)
		if name == "ws2_32" then
			error("cannot load ws2_32")
		end
		return original_load(name)
	end
	local ffi = winffi.new(real)
	check("fallback skips an unloadable library", ffi.C.fabs_like() == "ucrt")
end

return failures
