#!/usr/bin/env python3
"""
Scripture Browser — the filter.

An allow-list HTTP proxy on loopback.  The browser is locked to it in the
autoconfig (`network.proxy.*`, locked prefs), so every page it opens is asked
about here first, and a host that is not on the list never gets a connection.
It fails closed: if this is not running, the browser reaches nothing.

It speaks just enough HTTP to be a proxy:

  CONNECT host:port   a tunnel, for https.  The *host* is checked, and then the
                      bytes pass through untouched — no certificate is ever
                      impersonated, and nothing is decrypted.
  GET http://host/…   forwarded, with the request line rewritten.

It is not a cache and not a relay: it listens on loopback only, and forwards
only to hosts on its list.  The list is the same `allowlist.txt` the browser is
built from, flattened to hostnames by build.py into hosts.txt.

    filter.py --hosts /opt/scripture-browser/filter/hosts.txt --port 8791
"""

import argparse
import os
import select
import socket
import sys
import threading
import time
from urllib.parse import urlsplit

HEAD_LIMIT = 64 * 1024
IDLE_TIMEOUT = 60
CONNECT_TIMEOUT = 15
DENIAL_REPEAT_AFTER = 60          # seconds before the same blocked host is logged again


class Hosts:
    """hosts.txt, re-read when it changes, so adding a site does not need a restart."""

    def __init__(self, path):
        self.path = path
        self.mtime = 0
        self.set = set()
        self.logged = {}
        self.lock = threading.Lock()
        self.reload()

    def reload(self):
        try:
            mtime = os.stat(self.path).st_mtime
        except OSError as exc:
            print("cannot read %s: %s" % (self.path, exc), file=sys.stderr, flush=True)
            return False
        if mtime == self.mtime:
            return False
        with open(self.path, encoding="utf-8") as fh:
            hosts = {line.strip().lower().rstrip(".") for line in fh
                     if line.strip() and not line.startswith("#")}
        with self.lock:
            self.set = hosts
            self.mtime = mtime
        print("loaded %d hosts from %s" % (len(hosts), self.path), flush=True)
        return True

    def fresh(self):
        with self.lock:
            age = time.time() - getattr(self, "checked", 0)
            if age < 1:
                return
            self.checked = time.time()
        self.reload()

    def ok(self, host):
        host = host.lower().rstrip(".")
        if host in ("127.0.0.1", "localhost", "::1", "[::1]"):
            return True                      # the start page, served on this machine
        with self.lock:
            return host in self.set

    def note_denial(self, host):
        """Say it once a minute.  Firefox retries a refused host hard, and a log
        that repeats itself into the thousands is not a log anybody reads."""
        now = time.time()
        with self.lock:
            last = self.logged.get(host, 0)
            if now - last < DENIAL_REPEAT_AFTER:
                return False
            self.logged[host] = now
            return True


def read_head(conn):
    """Read up to the blank line.  Returns (head, leftover) or (None, None)."""
    conn.settimeout(CONNECT_TIMEOUT)
    data = b""
    while b"\r\n\r\n" not in data:
        try:
            chunk = conn.recv(4096)
        except (socket.timeout, OSError):
            return None, None
        if not chunk:
            return None, None
        data += chunk
        if len(data) > HEAD_LIMIT:
            return None, None
    head, leftover = data.split(b"\r\n\r\n", 1)
    return head, leftover


def split_host(target, headers, method):
    """Work out which host this request is for."""
    if method == "CONNECT":
        host = target
    elif "://" in target:
        host = urlsplit(target).netloc or urlsplit(target).path
    elif target.startswith("/"):
        # Origin form — a plain GET where the browser already knows the host and
        # only sent the path.  The Host header is the only thing that says where.
        host = ""
    else:
        host = target
    host = host.split("@")[-1]                      # user:pass@host
    if host.startswith("["):                        # [::1]:443
        host = host[1:].split("]")[0]
    else:
        host = host.split(":")[0]
    if not host:
        for line in headers.split(b"\r\n")[1:]:
            if line.lower().startswith(b"host:"):
                host = line.split(b":", 1)[1].strip().decode("latin-1")
                break
    return host.split(":")[0].strip().lower().rstrip(".")


def pump(client, upstream, leftover=b""):
    """Both directions until either side is done.  Nothing is inspected."""
    if leftover:
        upstream.sendall(leftover)
    client.setblocking(False)
    upstream.setblocking(False)
    try:
        while True:
            readable, _, _ = select.select([client, upstream], [], [], IDLE_TIMEOUT)
            if not readable:
                return
            for source in readable:
                try:
                    data = source.recv(65536)
                except OSError:
                    return
                if not data:
                    return
                (upstream if source is client else client).sendall(data)
    except OSError:
        return
    finally:
        for sock in (client, upstream):
            try:
                sock.setblocking(True)
            except OSError:
                pass


DENY_PAGE = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>Not on the list</title>
<style>
 body {{ background:#12100e; color:#ece3d4; font:16px/1.7 Georgia, serif;
        margin:0; padding:4rem 2rem; }}
 .box {{ max-width:34rem; margin:0 auto; }}
 h1 {{ font-size:1.4rem; font-weight:400; letter-spacing:.06em; color:#c9a45c;
       margin:0 0 1rem; }}
 p {{ color:#9c9083; margin:0 0 1rem; }}
 code {{ color:#ece3d4; background:#1b1815; border:1px solid #2c2721;
         padding:.15rem .4rem; border-radius:2px; font-size:.85rem; }}
 .host {{ color:#c9a45c; }}
</style></head><body><div class="box">
<h1>That site is not on the list</h1>
<p><span class="host">{host}</span> is not one of the sites this browser may open,
so the connection was refused before it was made.</p>
<p>If it belongs here, add it on the machine that administers this browser:</p>
<p><code>sudo scripture-browser --add {host}</code></p>
</div></body></html>
"""


def deny(conn, host, method, hosts):
    if hosts.note_denial(host):
        print("DENY  %s %s" % (method, host), flush=True)
    try:
        if method == "CONNECT":
            conn.sendall(b"HTTP/1.1 403 Forbidden\r\n"
                         b"Content-Length: 0\r\nConnection: close\r\n\r\n")
        else:
            body = DENY_PAGE.format(host=host).encode("utf-8")
            conn.sendall(b"HTTP/1.1 403 Forbidden\r\n"
                         b"Content-Type: text/html; charset=utf-8\r\n"
                         b"Content-Length: %d\r\nConnection: close\r\n\r\n" % len(body) + body)
    except OSError:
        pass


def handle(conn, hosts, verbose):
    with conn:
        head, leftover = read_head(conn)
        if head is None:
            return
        try:
            method, target, _version = head.split(b"\r\n")[0].split()
        except ValueError:
            return
        method = method.decode("latin-1").upper()
        target = target.decode("latin-1")

        host = split_host(target, head, method)
        hosts.fresh()

        if not hosts.ok(host):
            deny(conn, host, method, hosts)
            return

        if verbose:
            print("ALLOW %s %s" % (method, host), flush=True)

        port = 443 if method == "CONNECT" else 80
        if method != "CONNECT":
            try:
                port = urlsplit(target).port or 80
            except ValueError:
                port = 80
        elif ":" in target and not target.startswith("["):
            try:
                port = int(target.rsplit(":", 1)[1])
            except ValueError:
                port = 443

        try:
            upstream = socket.create_connection((host, port), timeout=CONNECT_TIMEOUT)
        except OSError as exc:
            if verbose:
                print("  %s unreachable: %s" % (host, exc), flush=True)
            return

        with upstream:
            if method == "CONNECT":
                conn.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                pump(conn, upstream, leftover)
                return

            # Plain http: rewrite the absolute request line to an origin-form one,
            # drop the hop-by-hop headers, and pass the rest through.
            lines = head.split(b"\r\n")
            path = target
            if "://" in target:
                parts = urlsplit(target)
                path = parts.path or "/"
                if parts.query:
                    path += "?" + parts.query
            keep = [lines[0].split(b" ")[0] + b" " + path.encode("latin-1") + b" HTTP/1.1"]
            for line in lines[1:]:
                low = line.lower()
                if low.startswith((b"proxy-connection:", b"proxy-authorization:")):
                    continue
                keep.append(line)
            upstream.sendall(b"\r\n".join(keep) + b"\r\n\r\n" + leftover)
            pump(conn, upstream)


def main():
    parser = argparse.ArgumentParser(description="The Scripture Browser allow-list proxy.")
    parser.add_argument("--hosts", required=True)
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8791)
    parser.add_argument("--verbose", action="store_true",
                        help="also log what was allowed, not just what was refused")
    args = parser.parse_args()

    hosts = Hosts(args.hosts)

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind((args.bind, args.port))
    listener.listen(128)
    print("filter listening on %s:%d" % (args.bind, args.port), flush=True)

    while True:
        try:
            conn, _ = listener.accept()
        except OSError:
            continue
        threading.Thread(target=handle, args=(conn, hosts, args.verbose),
                         daemon=True).start()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
