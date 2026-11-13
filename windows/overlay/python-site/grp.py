"""BunkerWeb for Windows: minimal grp emulation (every lookup succeeds, gid 1)."""

from collections import namedtuple

struct_group = namedtuple("struct_group", "gr_name gr_passwd gr_gid gr_mem")


def getgrnam(name):
    return struct_group(str(name), "x", 1, [])


def getgrgid(gid):
    return struct_group("nginx", "x", int(gid) if str(gid).isdigit() else 1, [])


def getgrall():
    return [getgrgid(1)]
