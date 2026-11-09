"""Linux development-filter evidence; not an application sandbox claim."""
import json
from pathlib import Path
import subprocess
import sys
import pytest

GUARD = Path(__file__).resolve().parents[1] / 'scripts' / 'run_network_denied.py'
pytestmark = pytest.mark.skipif(not sys.platform.startswith('linux'), reason='Linux child-only seccomp verification')


def run_guard(program):
    return subprocess.run([sys.executable, str(GUARD), '--', sys.executable, '-c', program],
                          capture_output=True, text=True, timeout=15)


def test_guard_denies_socket_in_exec_child_but_allows_file_work(tmp_path):
    output = tmp_path / 'local.json'
    code = '''import socket,errno,json,pathlib
results=[]
for family in (socket.AF_INET,socket.AF_INET6,socket.AF_UNIX):
 try: socket.socket(family,socket.SOCK_STREAM)
 except OSError as e: results.append(e.errno)
 else: raise AssertionError('socket was permitted')
assert results==[errno.EPERM]*3
pathlib.Path(%r).write_text(json.dumps(results))
''' % str(output)
    result = run_guard(code)
    assert result.returncode == 0, result.stderr
    assert 'filter loaded' in result.stderr
    assert json.loads(output.read_text()) == [1, 1, 1]


def test_guard_is_inherited_by_descendants():
    result = run_guard('import subprocess,sys; r=subprocess.run([sys.executable,"-c","import socket; socket.socket()"],capture_output=True,text=True); assert r.returncode!=0 and "Operation not permitted" in r.stderr')
    assert result.returncode == 0, result.stderr


def test_guard_missing_command_fails_closed():
    result = subprocess.run([sys.executable, str(GUARD)], capture_output=True, text=True, timeout=15)
    assert result.returncode == 2
    assert 'Provide the explicit local test command' in result.stderr


def test_guard_closes_inherited_descriptor_above_lowered_soft_limit():
    import os
    import resource
    import socket
    import fcntl
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    if soft <= 512:
        pytest.skip('Insufficient descriptor limit for the high-FD contract')
    left, right = socket.socketpair()
    high = fcntl.fcntl(left.fileno(), fcntl.F_DUPFD, 512)
    try:
        def lower_limit():
            soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
            resource.setrlimit(resource.RLIMIT_NOFILE, (128, hard))
        code = f'import os,errno\ntry: os.fstat({high})\nexcept OSError as e: assert e.errno==errno.EBADF\nelse: raise AssertionError("inherited high socket survived")'
        result = subprocess.run([sys.executable, str(GUARD), '--', sys.executable, '-c', code],
                                pass_fds=(high,), preexec_fn=lower_limit,
                                capture_output=True, text=True, timeout=15)
        assert result.returncode == 0, result.stderr
    finally:
        os.close(high); left.close(); right.close()


def test_denied_sendto_preserves_pipe_fallback_without_allowing_socket_io():
    code = """import ctypes,errno,os,socket
libc=ctypes.CDLL(None,use_errno=True)
libc.send.argtypes=[ctypes.c_int,ctypes.c_void_p,ctypes.c_size_t,ctypes.c_int]
libc.send.restype=ctypes.c_ssize_t
r,w=os.pipe()
try:
 assert libc.send(w,b'x',1,0)==-1
 assert ctypes.get_errno()==errno.ENOTSOCK
 assert os.write(w,b'x')==1 and os.read(r,1)==b'x'
 # Even an invalid descriptor receives the filter error: send never executes.
 assert libc.send(-1,b'x',1,0)==-1 and ctypes.get_errno()==errno.ENOTSOCK
 try: socket.socketpair()
 except OSError as e: assert e.errno==errno.EPERM
 else: raise AssertionError('socketpair unexpectedly permitted')
finally: os.close(r);os.close(w)
"""
    result=run_guard(code)
    assert result.returncode==0,result.stderr


def test_guard_keeps_other_syscalls_denied_and_errno_in_descendant():
    code = '''import ctypes,errno,subprocess,sys
libc=ctypes.CDLL(None,use_errno=True)
for name,args in [('connect',(-1,None,0)),('sendmsg',(-1,None,0)),('recvfrom',(-1,None,0,0,None,None))]:
 assert getattr(libc,name)(*args)==-1 and ctypes.get_errno()==errno.EPERM,name
seccomp=ctypes.CDLL('libseccomp.so.2')
seccomp.seccomp_syscall_resolve_name.argtypes=[ctypes.c_char_p]
seccomp.seccomp_syscall_resolve_name.restype=ctypes.c_int
for name in (b'io_uring_setup',b'io_uring_enter',b'io_uring_register'):
 number=seccomp.seccomp_syscall_resolve_name(name)
 assert number>=0
 assert libc.syscall(number,0,0,0,0,0,0)==-1 and ctypes.get_errno()==errno.EPERM
child="import ctypes,errno; c=ctypes.CDLL(None,use_errno=True); assert c.sendto(-1,None,0,0,None,0)==-1 and ctypes.get_errno()==errno.ENOTSOCK"
assert subprocess.run([sys.executable,'-c',child]).returncode==0
'''
    result=run_guard(code)
    assert result.returncode==0,result.stderr


def test_guard_rejects_socket_backed_standard_input():
    import socket
    left,right=socket.socketpair()
    try:
        result=subprocess.run([sys.executable,str(GUARD),'--',sys.executable,'-c','print("UNSAFE")'],
                              stdin=left,capture_output=True,text=True,timeout=15)
        assert result.returncode==2
        assert 'Socket-backed standard streams' in result.stderr
        assert 'UNSAFE' not in result.stdout
    finally:
        left.close();right.close()
