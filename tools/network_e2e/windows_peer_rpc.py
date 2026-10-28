"""Loopback RPC inside a disposable Windows container; never included in AKENO.

The host calls this through docker exec stdin, without publishing any RPC port.
Ephemeral fixture keys enter through stdin and never appear in argv or logs.
"""
from __future__ import annotations

import base64
import json
import socket
import socketserver
import sys
from pathlib import Path

PORT = 59998
LIMIT = 2 * 1024 * 1024


def encode(value):
    if isinstance(value, bytes):
        return {'_bytes': base64.b64encode(value).decode('ascii')}
    if isinstance(value, dict):
        return {k: encode(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [encode(v) for v in value]
    return value


def decode(value):
    if isinstance(value, dict):
        if set(value) == {'_bytes'}:
            return base64.b64decode(value['_bytes'], validate=True)
        return {k: decode(v) for k, v in value.items()}
    if isinstance(value, list):
        return [decode(v) for v in value]
    return value


def dispatch(peer, request):
    method, args = request['method'], request.get('args', [])
    if method == 'ready':
        return {'port': peer.port}
    if method == 'prove':
        return peer.prove(args[0], tuple(args[1]))
    if method == 'keepalive':
        return peer.keepalive()
    if method == 'send_ip':
        return peer.send_ip(args[0])
    if method == 'inbox':
        return peer.inbox_snapshot()
    if method == 'session':
        session = peer._member_session()
        return list(session.addr) if session else None
    if method == 'set_address':
        peer._member_session().addr = tuple(args[0])
        return None
    if method == 'accept_from':
        peer.accept_from = {tuple(item) for item in args[0]}
        return None
    raise ValueError('Unsupported fixture method.')


def serve(config):
    import wgpeer
    peer = wgpeer.Responder(config['private_key'], config['client_public'], config['addresses'], host='0.0.0.0').start()

    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            raw = self.rfile.readline(LIMIT + 1)
            if not raw.endswith(b'\n') or len(raw) > LIMIT:
                return
            try:
                result = {'result': encode(dispatch(peer, decode(json.loads(raw))))}
            except Exception as exc:
                result = {'error': type(exc).__name__}
            self.wfile.write(json.dumps(result).encode('ascii') + b'\n')

    with socketserver.TCPServer(('127.0.0.1', PORT), Handler) as server:
        try:
            server.serve_forever()
        finally:
            peer.stop()


if __name__ == '__main__':
    if sys.argv[1:] == ['--call']:
        raw = sys.stdin.buffer.read(LIMIT + 1)
        if len(raw) > LIMIT:
            raise SystemExit(2)
        with socket.create_connection(('127.0.0.1', PORT), timeout=5) as channel:
            channel.sendall(raw + b'\n')
            response = channel.makefile('rb').readline(LIMIT + 1)
            if len(response) > LIMIT:
                raise SystemExit(2)
            sys.stdout.buffer.write(response)
    elif len(sys.argv) == 3 and sys.argv[1] == '--serve':
        path = Path(sys.argv[2])
        config = json.loads(path.read_text(encoding='utf-8'))
        path.unlink()
        serve(config)
    else:
        raise SystemExit(2)
