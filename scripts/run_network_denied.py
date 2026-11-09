"""Linux-only development harness: deny network syscalls in this child chain.

No machine-wide settings are changed. Not used by the application. Fails closed
if libseccomp, filter loading or the socket negative control is unavailable.
Run: python scripts/run_network_denied.py -- python other_test.py ...
"""
from __future__ import annotations
import ctypes
import errno
import os
import socket
import sys

DENIED = ('socket', 'socketpair', 'connect', 'bind', 'listen', 'accept', 'accept4',
          'sendto', 'sendmsg', 'sendmmsg', 'recvfrom', 'recvmsg', 'recvmmsg',
          'shutdown', 'getsockopt', 'setsockopt', 'getpeername', 'getsockname',
          'io_uring_setup', 'io_uring_enter', 'io_uring_register')


def install_filter():
    if not sys.platform.startswith('linux'):
        raise RuntimeError('This development filter requires Linux.')
    lib = ctypes.CDLL('libseccomp.so.2', use_errno=True)
    lib.seccomp_init.argtypes = [ctypes.c_uint32]; lib.seccomp_init.restype = ctypes.c_void_p
    lib.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]; lib.seccomp_syscall_resolve_name.restype = ctypes.c_int
    lib.seccomp_rule_add.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int, ctypes.c_uint]
    lib.seccomp_load.argtypes = [ctypes.c_void_p]
    lib.seccomp_release.argtypes = [ctypes.c_void_p]
    context = lib.seccomp_init(0x7fff0000)  # SCMP_ACT_ALLOW except explicit denies.
    if not context: raise RuntimeError('Could not allocate syscall filter.')
    try:
        for name in DENIED:
            number = lib.seccomp_syscall_resolve_name(name.encode())
            # libpulse probes send() on an ordinary pipe before falling back to
            # write() on ENOTSOCK. Deny sendto without executing it, but preserve
            # this expected errno so Qt's local wake-up pipe does not deadlock.
            # No sockets or socketpairs are permitted by this exception.
            denied_errno = errno.ENOTSOCK if name == 'sendto' else errno.EPERM
            if number < 0 or lib.seccomp_rule_add(context, 0x00050000 | denied_errno, number, 0) != 0:
                raise RuntimeError('Cannot deny required syscall: ' + name)
        if lib.seccomp_load(context) != 0:
            raise RuntimeError('Kernel refused child-only syscall filter.')
    finally:
        lib.seccomp_release(context)
    for family, kind in ((socket.AF_INET, socket.SOCK_STREAM),
                         (socket.AF_INET6, socket.SOCK_DGRAM),
                         (socket.AF_UNIX, socket.SOCK_STREAM)):
        try:
            probe = socket.socket(family, kind)
        except OSError as exc:
            if exc.errno == errno.EPERM: continue
            raise RuntimeError('Unexpected negative-control failure.') from exc
        else:
            probe.close(); raise RuntimeError('Socket negative control unexpectedly succeeded.')


def main(argv=None):
    command = list(sys.argv[1:] if argv is None else argv)
    if command and command[0] == '--': command.pop(0)
    if not command: raise ValueError('Provide the explicit local test command after --.')
    # Only file/pipe standard streams are retained. Never pass inherited sockets.
    import stat
    if any(stat.S_ISSOCK(os.fstat(fd).st_mode) for fd in (0, 1, 2)):
        raise RuntimeError('Socket-backed standard streams are not allowed.')
    # A lowered RLIMIT_NOFILE does not close descriptors opened above it.
    # Enumerate actual descriptors instead of assuming the soft limit bounds them.
    descriptors = [int(name) for name in os.listdir('/proc/self/fd')]
    for fd in descriptors:
        if fd <= 2: continue
        try: os.close(fd)
        except OSError as exc:
            if exc.errno != errno.EBADF: raise
    for name in os.listdir('/proc/self/fd'):
        fd = int(name)
        if fd <= 2: continue
        try: os.fstat(fd)
        except OSError as exc:
            if exc.errno == errno.EBADF: continue  # listdir's already-closed FD.
            raise
        raise RuntimeError('An inherited file descriptor remained open.')
    install_filter()
    print('Network-denied test: filter loaded; IPv4/IPv6/Unix socket controls denied; io_uring denied.', file=sys.stderr, flush=True)
    os.execvpe(command[0], command, os.environ.copy())


if __name__ == '__main__':
    try: main()
    except Exception as exc:
        print('Network-denied test refused: ' + str(exc), file=sys.stderr)
        raise SystemExit(2)
