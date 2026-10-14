#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Relay TCP for one allowed peer; SSH authentication remains at the target."""
import argparse
import ipaddress
import socket
import threading


def endpoint(value):
    try:
        host, port = value.rsplit(':', 1)
        ipaddress.IPv4Address(host)
        port = int(port)
        if not 1 <= port <= 65535:
            raise ValueError('port out of range')
        return host, port
    except ValueError as error:
        raise argparse.ArgumentTypeError('expected IPv4:port') from error


def copy(source, target):
    try:
        while data := source.recv(65536):
            target.sendall(data)
    except OSError:
        pass
    try:
        target.shutdown(socket.SHUT_WR)
    except OSError:
        pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--listen', type=endpoint, required=True)
    parser.add_argument('--target', type=endpoint, required=True)
    parser.add_argument('--peer', type=ipaddress.IPv4Address, required=True)
    args = parser.parse_args()

    def relay(client, peer):
        if peer[0] != str(args.peer):
            client.close()
            return
        try:
            with client, socket.create_connection(args.target, timeout=5) as remote:
                remote.settimeout(None)
                thread = threading.Thread(target=copy, args=(client, remote), daemon=True)
                thread.start()
                copy(remote, client)
                thread.join(2)
        except OSError as error:
            print(type(error).__name__, str(error), flush=True)

    with socket.socket() as server:
        server.bind(args.listen)
        server.listen(5)
        print('SSH relay listening on %s:%s' % args.listen, flush=True)
        while True:
            client, peer = server.accept()
            threading.Thread(target=relay, args=(client, peer), daemon=True).start()


if __name__ == '__main__':
    main()
