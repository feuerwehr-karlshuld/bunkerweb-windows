"""BunkerWeb for Windows: minimal fcntl emulation.

flock()/lockf() are implemented with LockFileEx on a single byte far beyond the end of any real
file, the same byte the Lua side (bunkerweb/wincompat.lua) locks, so Python and nginx workers
exclude each other exactly like flock(2) on Linux. Locks are bound to the OS handle, which matches
flock's "per open file description" semantics closely enough for BunkerWeb's lock files.
"""

import ctypes
import msvcrt
import os
from ctypes import wintypes

LOCK_SH = 1
LOCK_EX = 2
LOCK_NB = 4
LOCK_UN = 8

F_GETFD = 1
F_SETFD = 2
F_GETFL = 3
F_SETFL = 4
F_GETLK = 5
F_SETLK = 6
F_SETLKW = 7
FD_CLOEXEC = 1

_LOCK_OFFSET_HIGH = 0x40000000
_LOCKFILE_FAIL_IMMEDIATELY = 0x1
_LOCKFILE_EXCLUSIVE_LOCK = 0x2


class _OVERLAPPED(ctypes.Structure):
    _fields_ = [
        ("Internal", ctypes.c_size_t),
        ("InternalHigh", ctypes.c_size_t),
        ("Offset", wintypes.DWORD),
        ("OffsetHigh", wintypes.DWORD),
        ("hEvent", wintypes.HANDLE),
    ]


_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.LockFileEx.argtypes = (wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(_OVERLAPPED))
_kernel32.LockFileEx.restype = wintypes.BOOL
_kernel32.UnlockFileEx.argtypes = (wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(_OVERLAPPED))
_kernel32.UnlockFileEx.restype = wintypes.BOOL


def _handle(fd):
    if hasattr(fd, "fileno"):
        fd = fd.fileno()
    return msvcrt.get_osfhandle(fd)


def _overlapped():
    overlapped = _OVERLAPPED()
    overlapped.Offset = 0
    overlapped.OffsetHigh = _LOCK_OFFSET_HIGH
    return overlapped


def flock(fd, operation):
    handle = _handle(fd)
    # flock() converts an existing lock, so always drop ours first
    _kernel32.UnlockFileEx(handle, 0, 1, 0, ctypes.byref(_overlapped()))
    if operation & LOCK_UN:
        return None
    flags = 0
    if operation & LOCK_EX:
        flags |= _LOCKFILE_EXCLUSIVE_LOCK
    if operation & LOCK_NB:
        flags |= _LOCKFILE_FAIL_IMMEDIATELY
    if _kernel32.LockFileEx(handle, flags, 0, 1, 0, ctypes.byref(_overlapped())):
        return None
    error = ctypes.get_last_error()
    if error in (33, 158, 997):  # lock violation / not locked / io pending
        raise BlockingIOError(11, "Resource temporarily unavailable")
    raise OSError(error, ctypes.FormatError(error))


def lockf(fd, cmd, len=0, start=0, whence=0):  # noqa: A002 - mirrors the stdlib signature
    return flock(fd, cmd)


def fcntl(fd, cmd, arg=0):
    if isinstance(arg, (bytes, bytearray)):
        return bytes(arg)
    return 0


def ioctl(fd, request, arg=0, mutate_flag=True):
    if isinstance(arg, (bytes, bytearray)):
        return bytes(arg)
    return 0


__all__ = [name for name in dir() if not name.startswith("_") and name not in ("ctypes", "msvcrt", "os", "wintypes")]
