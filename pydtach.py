import argparse
import os
import pty
import select
import signal
import socket
import sys
import termios
import tty

DETACH_CHAR = chr(ord('\\') - 64)  # Ctrl-\ by default

class TerminalMode:
    def __init__(self):
        self.orig_attrs = None

    def __enter__(self):
        self.orig_attrs = termios.tcgetattr(sys.stdin)
        tty.setraw(sys.stdin.fileno())
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.orig_attrs:
            termios.tcsetattr(sys.stdin, termios.TCSADRAIN, self.orig_attrs)


def run_master(sock_path, argv, wait_attach):
    if os.path.exists(sock_path):
        os.unlink(sock_path)
    serv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    serv.bind(sock_path)
    serv.listen(1)

    pid, master_fd = pty.fork()
    if pid == 0:
        # child
        os.execvp(argv[0], argv)

    clients = []
    try:
        while True:
            rfds = [serv, master_fd] + clients
            r, _, _ = select.select(rfds, [], [])
            if serv in r:
                conn, _ = serv.accept()
                conn.setblocking(False)
                clients.append(conn)
                if not wait_attach:
                    os.write(conn.fileno(), b"\r\nAttached.\r\n")
            if master_fd in r:
                data = os.read(master_fd, 1024)
                if not data:
                    break
                for c in clients:
                    try:
                        c.sendall(data)
                    except OSError:
                        clients.remove(c)
                r.remove(master_fd)
            for c in list(clients):
                if c in r:
                    try:
                        data = c.recv(1024)
                    except OSError:
                        data = b''
                    if not data:
                        clients.remove(c)
                        c.close()
                        continue
                    os.write(master_fd, data)
    finally:
        for c in clients:
            c.close()
        serv.close()
        os.close(master_fd)
        os.unlink(sock_path)


def run_attach(sock_path):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.connect(sock_path)
        with TerminalMode():
            while True:
                r, _, _ = select.select([s, sys.stdin], [], [])
                if s in r:
                    data = s.recv(1024)
                    if not data:
                        return
                    os.write(sys.stdout.fileno(), data)
                if sys.stdin in r:
                    ch = os.read(sys.stdin.fileno(), 1)
                    if not ch:
                        return
                    if ch == DETACH_CHAR.encode():
                        return
                    s.sendall(ch)


def main():
    parser = argparse.ArgumentParser(description="Minimal dtach implementation in Python")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('-a', metavar='SOCK', help='attach to existing session')
    group.add_argument('-c', nargs=argparse.REMAINDER, metavar=('SOCK', 'CMD'),
                       help='create and run command, attach')
    group.add_argument('-n', nargs=argparse.REMAINDER, metavar=('SOCK', 'CMD'),
                       help='create and run command detached')
    group.add_argument('-A', nargs=argparse.REMAINDER, metavar=('SOCK', 'CMD'),
                       help='attach or create')
    args = parser.parse_args()

    if args.a:
        run_attach(args.a)
    elif args.c is not None:
        if len(args.c) < 2:
            parser.error("-c requires SOCK and CMD")
        sock = args.c[0]
        cmd = args.c[1:]
        run_master(sock, cmd, wait_attach=True)
        run_attach(sock)
    elif args.n is not None:
        if len(args.n) < 2:
            parser.error("-n requires SOCK and CMD")
        sock = args.n[0]
        cmd = args.n[1:]
        run_master(sock, cmd, wait_attach=False)
    elif args.A is not None:
        if len(args.A) < 2:
            parser.error("-A requires SOCK and CMD")
        sock = args.A[0]
        cmd = args.A[1:]
        if os.path.exists(sock):
            try:
                run_attach(sock)
                return
            except OSError:
                os.unlink(sock)
        run_master(sock, cmd, wait_attach=True)
        run_attach(sock)

if __name__ == '__main__':
    main()
