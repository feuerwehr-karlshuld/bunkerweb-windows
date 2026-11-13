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
--      Linux AF_INET6 value mapped to the Windows one),
--   3. the real ffi.C,
--   4. ws2_32, then ucrtbase.
-- If nothing resolves, the error of the real ffi.C is raised, so failures look like they always did.
--
-- Everything is injected (the real ffi table, the libc emulation), so this file is unit-tested on Linux with a fake ffi
-- (windows/build/test-port.py).

local M = {}

local LINUX_AF_INET6 = 10
local WINDOWS_AF_INET6 = 23

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
