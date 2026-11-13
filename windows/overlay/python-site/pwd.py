"""BunkerWeb for Windows: minimal pwd emulation (every lookup succeeds, uid/gid 1)."""

import os
from collections import namedtuple

struct_passwd = namedtuple("struct_passwd", "pw_name pw_passwd pw_uid pw_gid pw_gecos pw_dir pw_shell")


def _entry(name="nginx", uid=1):
    return struct_passwd(str(name), "x", int(uid) if str(uid).isdigit() else 1, 1, str(name), os.environ.get("USERPROFILE", "C:\\"), "C:\\usr\\bin\\sh.exe")


def getpwnam(name):
    return _entry(name=name)


def getpwuid(uid):
    return _entry(name=os.environ.get("USERNAME", "nginx"), uid=uid)


def getpwall():
    return [_entry()]
