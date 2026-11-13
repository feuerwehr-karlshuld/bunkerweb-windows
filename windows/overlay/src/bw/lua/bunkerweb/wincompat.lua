-- BunkerWeb for Windows: compatibility layer for nginx/LuaJIT built with MinGW-w64 (UCRT64).
--
-- Loaded first by init_by_lua (see windows/build/apply-windows-port.py). It provides:
--   * Windows paths for package.cpath and ffi.load ("/bunkerweb/usr/..." -> "C:\bunkerweb\usr\...")
--   * io.popen / os.execute running commands through busybox-w32 "sh -c"
--   * a tiny libc emulation (fopen/fileno/flock/fputs/fclose/fchmod) used by api.lua and the
--     crowdsec stream lock, backed by Win32 file handles and LockFileEx (flock semantics)
--   * kill()/get_master_pid() based on the named events nginx for Windows listens on
--   * uptime() replacing /proc/uptime
-- On any other OS this module is an empty table and changes nothing.

local M = {}

if jit.os ~= "Windows" then
	return M
end

local ffi = require "ffi"
local bit = require "bit"

local band = bit.band
local concat = table.concat
local rep = string.rep
local sub = string.sub

local DRIVE = "C:"
local ROOT = DRIVE .. "\\bunkerweb"
local BUSYBOX = ROOT .. "\\usr\\bin\\busybox.exe"
local PID_FILE = "/bunkerweb/var/run/bunkerweb/nginx.pid"

local function cdef(decl)
	pcall(ffi.cdef, decl)
end

cdef [[ typedef struct { uint32_t nLength; void *lpSecurityDescriptor; int bInheritHandle; } BW_SECURITY_ATTRIBUTES; ]]
cdef [[ typedef struct {
	uint32_t cb; char *lpReserved; char *lpDesktop; char *lpTitle;
	uint32_t dwX; uint32_t dwY; uint32_t dwXSize; uint32_t dwYSize;
	uint32_t dwXCountChars; uint32_t dwYCountChars; uint32_t dwFillAttribute; uint32_t dwFlags;
	uint16_t wShowWindow; uint16_t cbReserved2; uint8_t *lpReserved2;
	void *hStdInput; void *hStdOutput; void *hStdError;
} BW_STARTUPINFOA; ]]
cdef [[ typedef struct { void *hProcess; void *hThread; uint32_t dwProcessId; uint32_t dwThreadId; } BW_PROCESS_INFORMATION; ]]
cdef [[ typedef struct { uintptr_t Internal; uintptr_t InternalHigh; uint32_t Offset; uint32_t OffsetHigh; void *hEvent; } BW_OVERLAPPED; ]]
cdef [[ struct bw_wfile { void *h; }; ]]
cdef [[ int CreatePipe(void **hReadPipe, void **hWritePipe, BW_SECURITY_ATTRIBUTES *lpPipeAttributes, uint32_t nSize); ]]
cdef [[ int SetHandleInformation(void *hObject, uint32_t dwMask, uint32_t dwFlags); ]]
cdef [[ void *CreateFileA(const char *lpFileName, uint32_t dwDesiredAccess, uint32_t dwShareMode, BW_SECURITY_ATTRIBUTES *lpSecurityAttributes, uint32_t dwCreationDisposition, uint32_t dwFlagsAndAttributes, void *hTemplateFile); ]]
cdef [[ int CreateProcessA(const char *lpApplicationName, char *lpCommandLine, BW_SECURITY_ATTRIBUTES *lpProcessAttributes, BW_SECURITY_ATTRIBUTES *lpThreadAttributes, int bInheritHandles, uint32_t dwCreationFlags, void *lpEnvironment, const char *lpCurrentDirectory, BW_STARTUPINFOA *lpStartupInfo, BW_PROCESS_INFORMATION *lpProcessInformation); ]]
cdef [[ uint32_t WaitForSingleObject(void *hHandle, uint32_t dwMilliseconds); ]]
cdef [[ int GetExitCodeProcess(void *hProcess, uint32_t *lpExitCode); ]]
cdef [[ int CloseHandle(void *hObject); ]]
cdef [[ int ReadFile(void *hFile, void *lpBuffer, uint32_t nNumberOfBytesToRead, uint32_t *lpNumberOfBytesRead, BW_OVERLAPPED *lpOverlapped); ]]
cdef [[ int WriteFile(void *hFile, const void *lpBuffer, uint32_t nNumberOfBytesToWrite, uint32_t *lpNumberOfBytesWritten, BW_OVERLAPPED *lpOverlapped); ]]
cdef [[ int LockFileEx(void *hFile, uint32_t dwFlags, uint32_t dwReserved, uint32_t nNumberOfBytesToLockLow, uint32_t nNumberOfBytesToLockHigh, BW_OVERLAPPED *lpOverlapped); ]]
cdef [[ int UnlockFileEx(void *hFile, uint32_t dwReserved, uint32_t nNumberOfBytesToUnlockLow, uint32_t nNumberOfBytesToUnlockHigh, BW_OVERLAPPED *lpOverlapped); ]]
cdef [[ uint32_t GetLastError(void); ]]
cdef [[ void *OpenEventA(uint32_t dwDesiredAccess, int bInheritHandle, const char *lpName); ]]
cdef [[ int SetEvent(void *hEvent); ]]
cdef [[ uint64_t GetTickCount64(void); ]]
cdef [[ uint32_t GetEnvironmentVariableA(const char *lpName, char *lpBuffer, uint32_t nSize); ]]
cdef [[ int SetEnvironmentVariableA(const char *lpName, const char *lpValue); ]]

local k32 = ffi.load("kernel32")

local GENERIC_READ = 0x80000000
local GENERIC_WRITE = 0x40000000
local FILE_APPEND_DATA = 0x00000004
local FILE_SHARE_ALL = 0x00000007 -- read | write | delete
local CREATE_NEW = 1
local CREATE_ALWAYS = 2
local OPEN_EXISTING = 3
local OPEN_ALWAYS = 4
local FILE_ATTRIBUTE_NORMAL = 0x80
local HANDLE_FLAG_INHERIT = 0x1
local STARTF_USESTDHANDLES = 0x100
local CREATE_NO_WINDOW = 0x08000000
local INFINITE = 0xFFFFFFFF
local EVENT_MODIFY_STATE = 0x0002
local LOCKFILE_FAIL_IMMEDIATELY = 0x1
local LOCKFILE_EXCLUSIVE_LOCK = 0x2
-- flock() is emulated with a one byte lock far beyond any real file content, so the lock never
-- blocks reads or writes of the file (Windows byte range locks are mandatory). The Python side
-- (python-site/fcntl.py) locks the very same byte.
local LOCK_OFFSET_HIGH = 0x40000000

local function is_invalid(h)
	return h == nil or tonumber(ffi.cast("intptr_t", h)) == -1
end

-- "/bunkerweb/usr/share/x" -> "C:\bunkerweb\usr\share\x"; relative names and names with a drive are kept
local function winpath(path)
	if type(path) ~= "string" then
		return path
	end
	if sub(path, 1, 1) == "/" and sub(path, 2, 2) ~= "/" then
		path = DRIVE .. path
	end
	return (path:gsub("/", "\\"))
end
M.winpath = winpath

-- ------------------------------------------------------------------ package.cpath / ffi.load
do
	local entries = {}
	for entry in (package.cpath .. ";"):gmatch("([^;]*);") do
		entries[#entries + 1] = entry:find("^/") and winpath(entry) or entry
	end
	package.cpath = concat(entries, ";")

	if not ffi.bw_wincompat_load then
		local original_load = ffi.load
		ffi.bw_wincompat_load = original_load
		ffi.load = function(name, global)
			if type(name) == "string" and name:find("/", 1, true) then
				name = winpath(name)
			end
			return original_load(name, global)
		end
	end
end

-- ------------------------------------------------------------------ process spawning
-- Quoting according to the MSVCRT command line parsing rules
local function quote_arg(arg)
	if arg ~= "" and not arg:find('[ \t\n\v"]') then
		return arg
	end
	local out = { '"' }
	local backslashes = 0
	for i = 1, #arg do
		local c = sub(arg, i, i)
		if c == "\\" then
			backslashes = backslashes + 1
		elseif c == '"' then
			out[#out + 1] = rep("\\", backslashes * 2 + 1) .. '"'
			backslashes = 0
		else
			out[#out + 1] = rep("\\", backslashes) .. c
			backslashes = 0
		end
	end
	out[#out + 1] = rep("\\", backslashes * 2) .. '"'
	return concat(out)
end

local function inheritable_sa()
	local sa = ffi.new("BW_SECURITY_ATTRIBUTES")
	sa.nLength = ffi.sizeof(sa)
	sa.bInheritHandle = 1
	return sa
end

-- Runs `cmd` with "busybox sh -c". Returns stdout (if capture) and the exit code, or nil + error.
local function run(cmd, capture)
	local sa = inheritable_sa()
	local null = k32.CreateFileA("NUL", bit.bor(GENERIC_READ, GENERIC_WRITE), 3, sa, OPEN_EXISTING, 0, nil)
	if is_invalid(null) then
		return nil, "cannot open NUL (error " .. k32.GetLastError() .. ")"
	end

	local read_end, write_end
	if capture then
		local r, w = ffi.new("void *[1]"), ffi.new("void *[1]")
		if k32.CreatePipe(r, w, sa, 0) == 0 then
			k32.CloseHandle(null)
			return nil, "CreatePipe failed (error " .. k32.GetLastError() .. ")"
		end
		read_end, write_end = r[0], w[0]
		k32.SetHandleInformation(read_end, HANDLE_FLAG_INHERIT, 0)
	end

	local si = ffi.new("BW_STARTUPINFOA")
	si.cb = ffi.sizeof(si)
	si.dwFlags = STARTF_USESTDHANDLES
	si.hStdInput = null
	si.hStdOutput = write_end or null
	si.hStdError = null
	local pi = ffi.new("BW_PROCESS_INFORMATION")

	local cmdline = quote_arg(BUSYBOX) .. " sh -c " .. quote_arg(cmd)
	local cmdbuf = ffi.new("char[?]", #cmdline + 1, cmdline)

	-- nginx for Windows marks its worker processes with the ngx_unique environment variable. A
	-- child that inherits it (e.g. "nginx -t" run by the API) would believe it is a worker.
	local envbuf = ffi.new("char[64]")
	local envlen = k32.GetEnvironmentVariableA("ngx_unique", envbuf, 64)
	local ngx_unique = (envlen > 0 and envlen < 64) and ffi.string(envbuf, envlen) or nil
	if ngx_unique then
		k32.SetEnvironmentVariableA("ngx_unique", nil)
	end
	local created = k32.CreateProcessA(nil, cmdbuf, nil, nil, 1, CREATE_NO_WINDOW, nil, nil, si, pi)
	local create_error = k32.GetLastError()
	if ngx_unique then
		k32.SetEnvironmentVariableA("ngx_unique", ngx_unique)
	end

	k32.CloseHandle(null)
	if write_end then
		k32.CloseHandle(write_end)
	end
	if created == 0 then
		if read_end then
			k32.CloseHandle(read_end)
		end
		return nil, "CreateProcess(" .. BUSYBOX .. ") failed (error " .. create_error .. ")"
	end
	k32.CloseHandle(pi.hThread)

	local output
	if read_end then
		local chunks = {}
		local buf = ffi.new("uint8_t[65536]")
		local got = ffi.new("uint32_t[1]")
		while k32.ReadFile(read_end, buf, 65536, got, nil) ~= 0 and got[0] > 0 do
			chunks[#chunks + 1] = ffi.string(buf, got[0])
		end
		k32.CloseHandle(read_end)
		output = concat(chunks):gsub("\r\n", "\n")
	end

	k32.WaitForSingleObject(pi.hProcess, INFINITE)
	local code = ffi.new("uint32_t[1]")
	k32.GetExitCodeProcess(pi.hProcess, code)
	k32.CloseHandle(pi.hProcess)
	return output, tonumber(code[0])
end
M.run = run

-- A read-only, fully buffered stand-in for the file handle io.popen returns
local function make_pipe(data, code)
	local pos = 1
	local len = #data
	local pipe = {}

	local function read_one(fmt)
		if type(fmt) == "number" then
			if pos > len then
				return nil
			end
			if fmt == 0 then
				return ""
			end
			local chunk = sub(data, pos, pos + fmt - 1)
			pos = pos + fmt
			return chunk
		end
		fmt = tostring(fmt or "l"):gsub("^%*", "")
		local kind = sub(fmt, 1, 1)
		if kind == "a" then
			local chunk = sub(data, pos)
			pos = len + 1
			return chunk
		elseif kind == "n" then
			local s, e = data:find("^%s*[-+]?[%d%.xXaAbBcCdDeEfFpP]+", pos)
			if not s then
				return nil
			end
			pos = e + 1
			return tonumber((sub(data, s, e):gsub("^%s+", "")))
		end
		if pos > len then
			return nil
		end
		local nl = data:find("\n", pos, true)
		local line
		if nl then
			line = kind == "L" and sub(data, pos, nl) or sub(data, pos, nl - 1)
			pos = nl + 1
		else
			line = sub(data, pos)
			pos = len + 1
		end
		return line
	end

	function pipe:read(...)
		local n = select("#", ...)
		if n <= 1 then
			return read_one((...))
		end
		local results = {}
		for i = 1, n do
			results[i] = read_one((select(i, ...)))
			if results[i] == nil then
				break
			end
		end
		return unpack(results, 1, n)
	end

	function pipe:lines(fmt)
		return function()
			return read_one(fmt or "l")
		end
	end

	function pipe:close()
		return true, "exit", code
	end

	function pipe:write()
		return nil, "pipe opened for reading"
	end

	function pipe:flush()
		return true
	end

	function pipe:setvbuf()
		return true
	end

	function pipe:seek()
		return nil, "cannot seek a pipe"
	end

	return pipe
end

if not io.bw_wincompat then
	io.bw_wincompat = true
	local original_execute = os.execute
	local lua52_execute = type(original_execute()) == "boolean"

	io.popen = function(cmd, mode)
		if mode and sub(mode, 1, 1) == "w" then
			return nil, "io.popen write mode is not supported on Windows"
		end
		local output, code = run(cmd, true)
		if output == nil then
			return nil, code
		end
		return make_pipe(output, code)
	end

	os.execute = function(cmd)
		if cmd == nil then
			return lua52_execute or 1
		end
		local _, code = run(cmd, false)
		if type(code) ~= "number" then
			code = 127
		end
		if lua52_execute then
			return code == 0 and true or nil, "exit", code
		end
		return code
	end
end

-- ------------------------------------------------------------------ libc emulation
local libc = {}
M.libc = libc

local function set_errno_from_win32(err)
	local errno = 5 -- EIO
	if err == 2 or err == 3 then
		errno = 2 -- ENOENT
	elseif err == 80 or err == 183 then
		errno = 17 -- EEXIST
	elseif err == 32 or err == 33 then
		errno = 11 -- EAGAIN
	elseif err == 5 then
		errno = 13 -- EACCES
	end
	ffi.errno(errno)
end

function libc.fopen(path, mode)
	mode = tostring(mode or "r")
	local kind = sub(mode, 1, 1)
	local plus = mode:find("+", 1, true) ~= nil
	local exclusive = mode:find("x", 1, true) ~= nil
	local access, disposition
	if kind == "r" then
		access = plus and bit.bor(GENERIC_READ, GENERIC_WRITE) or GENERIC_READ
		disposition = OPEN_EXISTING
	elseif kind == "w" then
		access = plus and bit.bor(GENERIC_READ, GENERIC_WRITE) or GENERIC_WRITE
		disposition = exclusive and CREATE_NEW or CREATE_ALWAYS
	elseif kind == "a" then
		-- GENERIC_READ keeps LockFileEx usable on append handles
		access = bit.bor(GENERIC_READ, FILE_APPEND_DATA)
		disposition = exclusive and CREATE_NEW or OPEN_ALWAYS
	else
		ffi.errno(22) -- EINVAL
		return nil
	end
	local h = k32.CreateFileA(winpath(path), access, FILE_SHARE_ALL, nil, disposition, FILE_ATTRIBUTE_NORMAL, nil)
	if is_invalid(h) then
		set_errno_from_win32(k32.GetLastError())
		return nil
	end
	return ffi.new("struct bw_wfile", { h })
end

function libc.fileno(file)
	if file == nil or file.h == nil then
		return -1
	end
	return tonumber(ffi.cast("intptr_t", file.h))
end

function libc.flock(fd, operation)
	if type(fd) ~= "number" or fd < 0 then
		ffi.errno(9) -- EBADF
		return -1
	end
	local h = ffi.cast("void *", fd)
	local ov = ffi.new("BW_OVERLAPPED")
	ov.Offset = 0
	ov.OffsetHigh = LOCK_OFFSET_HIGH
	-- flock() converts an existing lock: drop ours first (fails harmlessly when there is none)
	k32.UnlockFileEx(h, 0, 1, 0, ov)
	if band(operation, 8) ~= 0 then -- LOCK_UN
		return 0
	end
	local flags = 0
	if band(operation, 2) ~= 0 then -- LOCK_EX
		flags = bit.bor(flags, LOCKFILE_EXCLUSIVE_LOCK)
	end
	if band(operation, 4) ~= 0 then -- LOCK_NB
		flags = bit.bor(flags, LOCKFILE_FAIL_IMMEDIATELY)
	end
	ov = ffi.new("BW_OVERLAPPED")
	ov.Offset = 0
	ov.OffsetHigh = LOCK_OFFSET_HIGH
	if k32.LockFileEx(h, flags, 0, 1, 0, ov) ~= 0 then
		return 0
	end
	local err = k32.GetLastError()
	if err == 33 or err == 158 or err == 997 then
		ffi.errno(11) -- EWOULDBLOCK
	else
		ffi.errno(5)
	end
	return -1
end

function libc.fputs(s, file)
	if file == nil or file.h == nil then
		return -1
	end
	local written = ffi.new("uint32_t[1]")
	if k32.WriteFile(file.h, s, #s, written, nil) == 0 then
		return -1
	end
	return tonumber(written[0])
end

function libc.fclose(file)
	if file ~= nil and file.h ~= nil then
		k32.CloseHandle(file.h)
		file.h = nil
	end
	return 0
end

function libc.fchmod()
	return 0
end

-- ------------------------------------------------------------------ signals and pid
local SIGNAL_EVENTS = {
	HUP = "reload",
	[1] = "reload",
	QUIT = "quit",
	[3] = "quit",
	TERM = "stop",
	[15] = "stop",
	INT = "stop",
	[2] = "stop",
	KILL = "stop",
	[9] = "stop",
	USR1 = "reopen",
	[10] = "reopen",
}

function M.kill(pid, sig)
	pid = tonumber(pid)
	if not pid then
		return nil, "invalid pid"
	end
	if type(sig) == "string" then
		sig = sig:upper():gsub("^SIG", "")
	end
	local event = SIGNAL_EVENTS[sig]
	if not event then
		return nil, "signal " .. tostring(sig) .. " is not supported on Windows"
	end
	local name = "Global\\ngx_" .. event .. "_" .. pid
	local h = k32.OpenEventA(EVENT_MODIFY_STATE, 0, name)
	if h == nil then
		return nil, "OpenEvent(" .. name .. ") failed (error " .. k32.GetLastError() .. ")"
	end
	local ok = k32.SetEvent(h)
	local err = k32.GetLastError()
	k32.CloseHandle(h)
	if ok == 0 then
		return nil, "SetEvent(" .. name .. ") failed (error " .. err .. ")"
	end
	return true
end

function M.get_master_pid()
	local file = io.open(PID_FILE, "r")
	if not file then
		return nil
	end
	local pid = tonumber((file:read("*a") or ""):match("(%d+)"))
	file:close()
	return pid
end

function M.uptime()
	return math.floor(tonumber(k32.GetTickCount64()) / 1000)
end

return M
