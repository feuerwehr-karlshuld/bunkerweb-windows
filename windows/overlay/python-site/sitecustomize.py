"""BunkerWeb for Windows: POSIX shims for the bundled Python interpreter.

Loaded automatically at interpreter start (site.py imports sitecustomize). BunkerWeb's Python
code was written for Linux; instead of patching hundreds of call sites, the few missing POSIX
pieces are provided here:

* uid/gid helpers, chown, setuid & co. as harmless no-ops (geteuid() is 1, never root)
* os.sched_getaffinity / os.getloadavg / O_CLOEXEC / O_NOFOLLOW
* os.rename with POSIX "replace" semantics
* os.kill(pid, 0) as a liveness probe (on Windows it would terminate the process)
* signal.SIGHUP (aliased to SIGBREAK, see windows/bwsvc.py)
* subprocess.Popen: run *.py / *.sh / shebang scripts through python / busybox sh, find *.exe,
  map /dev/null to NUL and keep the environment variables Windows processes need

Every attribute added to the os module is recorded in os._bw_shims so that programs which must
see the plain Windows behaviour (certbot) can remove them again.
"""

import sys

if sys.platform == "win32":
    import ctypes
    import os
    import signal
    import subprocess
    from ctypes import wintypes

    _BUSYBOX = os.environ.get("BW_BUSYBOX", r"C:\usr\bin\busybox.exe")
    _added = []

    def _add(name, value):
        if not hasattr(os, name):
            setattr(os, name, value)
            _added.append(name)

    _add("geteuid", lambda: 1)
    _add("getegid", lambda: 1)
    _add("getuid", lambda: 1)
    _add("getgid", lambda: 1)
    _add("getgroups", lambda: [1])
    _add("getgrouplist", lambda user, group: [group])
    _add("setuid", lambda uid: None)
    _add("setgid", lambda gid: None)
    _add("seteuid", lambda uid: None)
    _add("setegid", lambda gid: None)
    _add("setgroups", lambda groups: None)
    _add("initgroups", lambda user, group: None)
    _add("chown", lambda path, uid, gid, *, dir_fd=None, follow_symlinks=True: None)
    _add("lchown", lambda path, uid, gid: None)
    _add("fchown", lambda fd, uid, gid: None)
    _add("fchmod", lambda fd, mode: None)
    _add("sync", lambda: None)
    _add("sched_getaffinity", lambda pid: set(range(os.cpu_count() or 1)))
    _add("getloadavg", lambda: (0.0, 0.0, 0.0))
    _add("O_CLOEXEC", getattr(os, "O_NOINHERIT", 0))
    _add("O_NOFOLLOW", 0)
    _add("O_DIRECTORY", 0)
    _add("O_NONBLOCK", 0)
    os._bw_shims = _added

    # POSIX rename() replaces an existing destination, Windows' rename() refuses to
    os._bw_original_rename = os.rename
    os.rename = os.replace

    if not hasattr(signal, "SIGHUP"):
        signal.SIGHUP = signal.SIGBREAK

    # ------------------------------------------------------------------ os.kill(pid, 0)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    _kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    _kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    _original_kill = os.kill

    def _kill(pid, sig):
        if sig == 0:
            handle = _kernel32.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
            if not handle:
                if ctypes.get_last_error() == 5:  # access denied: the process exists
                    return None
                raise ProcessLookupError(3, "No such process")
            try:
                code = wintypes.DWORD()
                if _kernel32.GetExitCodeProcess(handle, ctypes.byref(code)) and code.value != 259:  # STILL_ACTIVE
                    raise ProcessLookupError(3, "No such process")
            finally:
                _kernel32.CloseHandle(handle)
            return None
        return _original_kill(pid, sig)

    os.kill = _kill

    # ------------------------------------------------------------------ subprocess
    _KEEP_ENV = (
        "SYSTEMROOT",
        "SYSTEMDRIVE",
        "WINDIR",
        "COMSPEC",
        "TEMP",
        "TMP",
        "PATHEXT",
        "PROGRAMDATA",
        "PROGRAMFILES",
        "USERPROFILE",
        "APPDATA",
        "LOCALAPPDATA",
        "OPENSSL_CONF",
        "MAGIC",
        "BW_BUSYBOX",
    )
    _SHELLS = {"sh", "bash", "/bin/sh", "/bin/bash", "/usr/bin/sh", "/usr/bin/bash"}

    def _script_interpreter(path):
        lower = path.lower()
        if lower.endswith((".exe", ".com", ".bat", ".cmd")):
            return None
        if lower.endswith(".py"):
            return [sys.executable]
        if lower.endswith(".sh"):
            return [_BUSYBOX, "sh"]
        try:
            with open(path, "rb") as file:
                first = file.readline(256)
        except OSError:
            return None
        if first.startswith(b"#!"):
            if b"python" in first:
                return [sys.executable]
            if b"sh" in first:
                return [_BUSYBOX, "sh"]
        return None

    def _translate(argv):
        if not argv or not isinstance(argv[0], (str, os.PathLike)):
            return argv
        program = os.fspath(argv[0])
        rest = argv[1:]
        if program in _SHELLS:
            argv = [_BUSYBOX, "sh", *rest]
        elif os.path.isfile(program):
            interpreter = _script_interpreter(program)
            if interpreter:
                argv = [*interpreter, os.path.abspath(program), *rest]
            elif "/" in program or "\\" in program:
                argv = [os.path.abspath(program), *rest]
        elif ("/" in program or "\\" in program) and os.path.isfile(program + ".exe"):
            argv = [os.path.abspath(program + ".exe"), *rest]
        return [("NUL" if arg == "/dev/null" else arg) for arg in argv]

    _original_popen_init = subprocess.Popen.__init__

    def _popen_init(self, args, *pargs, **kwargs):
        kwargs.pop("preexec_fn", None)
        kwargs.pop("pass_fds", None)
        env = kwargs.get("env")
        if env is not None:
            env = dict(env)
            present = {key.upper() for key in env}
            for key in _KEEP_ENV:
                value = os.environ.get(key)
                if value and key not in present:
                    env[key] = value
            kwargs["env"] = env
        if kwargs.get("shell") and isinstance(args, str):
            kwargs["shell"] = False
            args = [_BUSYBOX, "sh", "-c", args]
        elif isinstance(args, (list, tuple)):
            args = _translate(list(args))
        elif isinstance(args, (str, os.PathLike)) and not kwargs.get("shell") and os.path.isfile(os.fspath(args)):
            args = _translate([args])
        _original_popen_init(self, args, *pargs, **kwargs)

    subprocess.Popen.__init__ = _popen_init

    # ------------------------------------------------------------------ pywin32
    # The dependencies live in a --target directory that is only on PYTHONPATH, so its .pth file
    # (win32, win32\lib, Pythonwin, pywin32_bootstrap -> DLL directory) is never processed by site.
    import site

    for _entry in os.environ.get("PYTHONPATH", "").split(os.pathsep):
        if _entry and os.path.isfile(os.path.join(_entry, "pywin32.pth")):
            site.addpackage(_entry, "pywin32.pth", None)
