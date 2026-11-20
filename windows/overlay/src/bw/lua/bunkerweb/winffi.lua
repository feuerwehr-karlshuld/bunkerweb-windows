-- BunkerWeb for Windows: a drop-in replacement for the "ffi" module that makes ffi.C usable for the libc/winsock
-- functions BunkerWeb and its Lua libraries call on Linux.
--
-- On Windows, LuaJIT's ffi.C only searches the executable, lua51.dll, the CRT, kernel32, user32 and gdi32. Calls such as
-- inet_pton/ntohl (winsock), setenv/unsetenv/environ (not in the UCRT) or fopen/flock (different semantics) load fine
-- and fail on the first request. Instead of patching every call site in the sources, wincompat.lua installs the proxy
-- built here as package.loaded["ffi"], so everything that requires "ffi" afterwards gets it.
--
-- Resolution order for ffi.C.<name>:
--   1. getters (variables that must be re-read on every access, e.g. environ),
--   2. overrides (fopen/fileno/flock/fputs/fclose/fchmod emulation, setenv/unsetenv, inet_pton/inet_ntop with the
--      Linux AF_INET6 value mapped to the Windows one, open/write/close/getpid through the UCRT's _open/... with the
--      Linux O_* flags translated),
--   3. the real ffi.C,
--   4. ws2_32, then ucrtbase.
-- If nothing resolves, the error of the real ffi.C is raised, so failures look like they always did.
--
-- Everything is injected (the real ffi table, the libc emulation), so this file is unit-tested on Linux with a fake ffi
-- (windows/build/test-port.py).

local M = {}

local LINUX_AF_INET6 = 10
local WINDOWS_AF_INET6 = 23

-- open(2) flags: Linux value -> UCRT value. O_RDONLY/O_WRONLY/O_RDWR (the low two bits) are the same. Passing the Linux
-- values through would be wrong, not just incomplete: 0x40 (O_CREAT) is _O_TEMPORARY (delete on close) in the UCRT.
local LINUX_TO_UCRT_OPEN_FLAGS = {
	{ 0x40, 0x0100 }, -- O_CREAT -> _O_CREAT
	{ 0x80, 0x0400 }, -- O_EXCL -> _O_EXCL
	{ 0x200, 0x0200 }, -- O_TRUNC -> _O_TRUNC
	{ 0x400, 0x0008 }, -- O_APPEND -> _O_APPEND
	{ 0x80000, 0x0080 }, -- O_CLOEXEC -> _O_NOINHERIT
}
local UCRT_O_BINARY = 0x8000 -- never translate line endings, like Linux
local UCRT_S_IREAD = 0x100 -- same bit as the owner read bit 0400 of a POSIX mode
local UCRT_S_IWRITE = 0x80 -- same bit as the owner write bit 0200

-- No bit library: this file is also loaded by plain Lua in the unit tests
local function has_bit(value, mask)
	return math.floor(value / mask) % 2 == 1
end

function M.open_flags(flags)
	flags = tonumber(flags) or 0
	local result = flags % 4
	for _, pair in ipairs(LINUX_TO_UCRT_OPEN_FLAGS) do
		if has_bit(flags, pair[1]) then
			result = result + pair[2]
		end
	end
	return result + UCRT_O_BINARY
end

-- The UCRT only knows "read-only or not" (from the owner bits); group/other bits and the umask do not exist there
function M.open_mode(mode)
	mode = tonumber(mode)
	if not mode then
		return UCRT_S_IREAD + UCRT_S_IWRITE
	end
	return UCRT_S_IREAD + (has_bit(mode, UCRT_S_IWRITE) and UCRT_S_IWRITE or 0)
end

-- Libraries searched when the real ffi.C does not know a symbol, in this order
M.FALLBACK_LIBS = { "ws2_32", "ucrtbase" }

local function lazy_loader(real_ffi)
	local libs = {}
	return function(name)
		local lib = libs[name]
		if lib == nil then
			local ok, loaded = pcall(real_ffi.load, name)
			lib = ok and loaded or false
			libs[name] = lib
		end
		return lib or nil
	end
end

-- libc: the emulation table of wincompat.lua (fopen, fileno, flock, fputs, fclose, fchmod), optional
function M.new(real_ffi, libc)
	local load = lazy_loader(real_ffi)
	local real_c = real_ffi.C
	local overrides = {}
	local getters = {}
	local resolved = {} -- symbol name -> library namespace it was found in

	for name, fn in pairs(libc or {}) do
		overrides[name] = fn
	end

	local declared = {}
	local function declare(decl)
		if not declared[decl] then
			declared[decl] = true
			pcall(real_ffi.cdef, decl)
		end
	end

	-- inet_pton/inet_ntop: callers written for Linux pass AF_INET6 = 10, winsock wants 23
	local function af(value)
		return value == LINUX_AF_INET6 and WINDOWS_AF_INET6 or value
	end
	overrides.inet_pton = function(family, ...)
		return load("ws2_32").inet_pton(af(family), ...)
	end
	overrides.inet_ntop = function(family, ...)
		return load("ws2_32").inet_ntop(af(family), ...)
	end

	-- setenv/unsetenv/environ do not exist in the UCRT: _putenv_s changes the environment the CRT (and libModSecurity)
	-- see. 'environ' is re-read on every access, putenv may reallocate it.
	overrides.setenv = function(name, value)
		declare("int _putenv_s(const char *name, const char *value);")
		return load("ucrtbase")._putenv_s(name, value) == 0 and 0 or -1
	end
	overrides.unsetenv = function(name)
		declare("int _putenv_s(const char *name, const char *value);")
		return load("ucrtbase")._putenv_s(name, "") == 0 and 0 or -1
	end
	getters.environ = function()
		declare("char ***__p__environ(void);")
		return load("ucrtbase").__p__environ()[0]
	end

	-- POSIX file descriptor calls (CrowdSec's enforcement_state.lua writes its checkpoints this way since BunkerWeb
	-- 1.6.18): the UCRT exports them only under their _-prefixed names, which ffi.C does not alias.
	overrides.open = function(path, flags, mode)
		declare("int _open(const char *filename, int oflag, int pmode);")
		return load("ucrtbase")._open(path, M.open_flags(flags), M.open_mode(mode))
	end
	overrides.write = function(fd, buf, count)
		declare("int _write(int fd, const void *buffer, unsigned int count);")
		return load("ucrtbase")._write(fd, buf, count)
	end
	overrides.close = function(fd)
		declare("int _close(int fd);")
		return load("ucrtbase")._close(fd)
	end
	overrides.getpid = function()
		declare("int _getpid(void);")
		return load("ucrtbase")._getpid()
	end

	local function lookup(_, name)
		local getter = getters[name]
		if getter then
			return getter()
		end
		local override = overrides[name]
		if override ~= nil then
			return override
		end
		local known = resolved[name]
		if known then
			return known[name]
		end
		local ok, value = pcall(function()
			return real_c[name]
		end)
		if ok then
			resolved[name] = real_c
			return value
		end
		for _, lib_name in ipairs(M.FALLBACK_LIBS) do
			local lib = load(lib_name)
			if lib then
				local found, fallback = pcall(function()
					return lib[name]
				end)
				if found then
					resolved[name] = lib
					return fallback
				end
			end
		end
		return real_c[name] -- raises the original "cannot resolve symbol" error
	end

	local c_proxy = setmetatable({}, { __index = lookup })
	return setmetatable({ C = c_proxy }, {
		__index = real_ffi,
		__newindex = function(_, key, value)
			real_ffi[key] = value
		end,
	})
end

return M
