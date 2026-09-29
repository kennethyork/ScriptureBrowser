#!/usr/bin/env python3
"""
Scripture Browser — the start page, served on loopback.

Only needed if a Firefox build ever refuses to open the start page as a local
file.  On this machine it does not: the page is read straight off the disk.  It is
kept because it is small, sandboxed, and has been tested — see
`install.sh --home-server`.

It binds to 127.0.0.1, serves one directory, has no directory listing, keeps no
log, and is sandboxed by systemd.  It reads nothing but the page.
"""

import argparse
import http.server
import os
import socketserver
import sys


class Handler(http.server.SimpleHTTPRequestHandler):
    server_version = "ScriptureBrowser"
    sys_version = ""

    def list_directory(self, path):
        self.send_error(403, "No directory listing")
        return None

    def log_message(self, *args):
        pass

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        super().end_headers()


def main():
    parser = argparse.ArgumentParser(description="Serve the Scripture Browser start page.")
    parser.add_argument("--root", required=True, help="directory to serve")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()

    root = os.path.abspath(args.root)
    if not os.path.isfile(os.path.join(root, "index.html")):
        sys.exit("no index.html in %s" % root)

    handler = lambda *a, **kw: Handler(*a, directory=root, **kw)  # noqa: E731
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer((args.host, args.port), handler) as httpd:
        httpd.serve_forever()


if __name__ == "__main__":
    main()
