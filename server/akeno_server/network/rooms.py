"""Transactional LAN-room coordination prototype, for isolated tests and later authenticated API integration.

`actor` MUST come from the existing authenticated and licensed server session, never a request body. This
module exposes no endpoint and is not imported by the production app. It creates no adapter, tunnel, relay,
firewall rule or Internet exit. Virtual addresses below are allocations, not evidence of connectivity.
"""
from __future__ import annotations

import base64
import hashlib
import ipaddress
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError


class RoomError(ValueError):
    pass


class RoomStore:
    INVITE_TTL = 600
    ROOM_TTL = 86400
    PEER_LEASE = 45
    MAX_PEERS = 16

    def __init__(self, path: Path, clock=time.time):
        self.path, self.clock = Path(path), clock
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.passwords = PasswordHasher(time_cost=2, memory_cost=8192, parallelism=1)
        with self._tx() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS rooms (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, name TEXT NOT NULL, slot INTEGER UNIQUE NOT NULL,
                    password TEXT, invite TEXT UNIQUE NOT NULL, invite_expiry REAL NOT NULL, expiry REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS peers (
                    room TEXT REFERENCES rooms(id) ON DELETE CASCADE, actor TEXT NOT NULL, public_key TEXT NOT NULL,
                    host INTEGER NOT NULL, seen REAL NOT NULL, PRIMARY KEY(room,actor), UNIQUE(room,host),
                    UNIQUE(room,public_key));
                CREATE TABLE IF NOT EXISTS banned (
                    room TEXT REFERENCES rooms(id) ON DELETE CASCADE, actor TEXT NOT NULL, PRIMARY KEY(room,actor));
            ''')
            if 'device' not in {r[1] for r in db.execute('PRAGMA table_info(peers)')}:
                db.execute("ALTER TABLE peers ADD COLUMN device TEXT NOT NULL DEFAULT ''")

    @contextmanager
    def _tx(self):
        db=sqlite3.connect(self.path,timeout=10)
        db.row_factory=sqlite3.Row
        try:
            db.execute('PRAGMA foreign_keys=ON')
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _actor(actor):
        if not isinstance(actor,str) or not 1 <= len(actor) <= 128 or any(ord(c)<32 for c in actor):
            raise RoomError('Invalid authenticated actor.')

    @staticmethod
    def _key(public_key):
        try:
            raw=base64.b64decode(public_key,validate=True)
            if len(raw)!=32 or not any(raw) or base64.b64encode(raw).decode()!=public_key:
                raise ValueError()
        except (ValueError,TypeError):
            raise RoomError('Invalid public key.') from None

    @staticmethod
    def _hash(invite):
        if not isinstance(invite,str) or not 16 <= len(invite) <= 128:
            raise RoomError('Invalid or expired invitation.')
        return hashlib.sha256(invite.encode()).hexdigest()

    def _room(self,db,ident):
        room=db.execute('SELECT * FROM rooms WHERE id=?',(ident,)).fetchone()
        if not room or room['expiry']<=self.clock():
            raise RoomError('Room is unavailable.')
        return room

    def _member(self,db,room,actor):
        self._actor(actor)
        peer=db.execute('SELECT * FROM peers WHERE room=? AND actor=?',(room['id'],actor)).fetchone()
        if peer is None:
            raise RoomError('Membership required.')
        return peer

    def _owner(self,room,actor):
        self._actor(actor)
        if room['owner']!=actor:
            raise RoomError('Room owner required.')

    def create(self,actor,name,public_key,password='',device_id=''):
        self._actor(actor)
        self._key(public_key)
        if not isinstance(name,str) or not 1 <= len(name.strip()) <= 64 or any(ord(c)<32 for c in name):
            raise RoomError('Invalid room name.')
        if not isinstance(password,str) or len(password)>128:
            raise RoomError('Invalid room password.')
        # Hash before holding a database write lock; passwords never appear in returned state.
        password_hash=self.passwords.hash(password) if password else None
        ident,invite=secrets.token_hex(16),secrets.token_urlsafe(24)
        now=self.clock()
        with self._tx() as db:
            db.execute('DELETE FROM rooms WHERE expiry<=?',(now,))
            if db.execute('SELECT count(*) FROM rooms WHERE owner=?',(actor,)).fetchone()[0]>=3:
                raise RoomError('Room limit reached.')
            slots={r[0] for r in db.execute('SELECT slot FROM rooms')}
            slot=next((n for n in range(1,255) if n not in slots),None)
            if slot is None:
                raise RoomError('No room capacity.')
            db.execute('INSERT INTO rooms VALUES (?,?,?,?,?,?,?,?)',
                       (ident,actor,name.strip(),slot,password_hash,self._hash(invite),now+self.INVITE_TTL,
                        now+self.ROOM_TTL))
            db.execute('INSERT INTO peers VALUES (?,?,?,?,?,?)',(ident,actor,public_key,2,now,device_id))
        return {'room_id':ident,'invite':invite,'invite_expires_at':now+self.INVITE_TTL,
                'subnet':f'10.203.{slot}.0/24','transport':'not_implemented'}

    def join(self,actor,invite,public_key,password='',device_id=''):
        self._actor(actor)
        self._key(public_key)
        if not isinstance(password,str) or len(password)>128:
            raise RoomError('Invalid room password.')
        with self._tx() as db:
            room=db.execute('SELECT * FROM rooms WHERE invite=?',(self._hash(invite),)).fetchone()
            now=self.clock()
            if not room or min(room['invite_expiry'],room['expiry'])<=now:
                raise RoomError('Invalid or expired invitation.')
            ident=room['id']
            if db.execute('SELECT 1 FROM banned WHERE room=? AND actor=?',(ident,actor)).fetchone():
                raise RoomError('Membership revoked.')
            if room['password']:
                try:
                    self.passwords.verify(room['password'],password)
                except VerificationError:
                    raise RoomError('Invalid room password.') from None
            existing=db.execute('SELECT * FROM peers WHERE room=? AND actor=?',(ident,actor)).fetchone()
            if existing:
                if existing['public_key']!=public_key or existing['device']!=device_id:
                    raise RoomError('Leave the room before registering another public key.')
                db.execute('UPDATE peers SET seen=? WHERE room=? AND actor=?',(now,ident,actor))
                return ident
            hosts={p[0] for p in db.execute('SELECT host FROM peers WHERE room=?',(ident,))}
            if len(hosts)>=self.MAX_PEERS:
                raise RoomError('Room is full.')
            host=next(n for n in range(2,255) if n not in hosts)
            try:
                db.execute('INSERT INTO peers VALUES (?,?,?,?,?,?)',(ident,actor,public_key,host,now,device_id))
            except sqlite3.IntegrityError:
                raise RoomError('Public key is already registered.') from None
            return ident

    def members(self,actor,ident):
        with self._tx() as db:
            room=self._room(db,ident)
            self._member(db,room,actor)
            return [{'actor':r['actor'],'device_id':r['device'],'public_key':r['public_key'],
                     'virtual_ip':str(ipaddress.ip_address(f"10.203.{room['slot']}.{r['host']}")),
                     'status':'registered' if r['seen']+self.PEER_LEASE>self.clock() else 'expired',
                     'transport':'not_implemented'}
                    for r in db.execute('SELECT * FROM peers WHERE room=? ORDER BY host',(ident,))]

    def grant_for(self,public_key):
        """The live membership of a WireGuard key for the coordinator: room, actor, device and lease end (or None).
        A key active in several rooms counts for the one it renewed most recently."""
        with self._tx() as db:
            row=db.execute('SELECT peers.room,peers.actor,peers.device,peers.seen FROM peers JOIN rooms ON rooms.id=peers.room '
                           'WHERE peers.public_key=? AND rooms.expiry>? ORDER BY peers.seen DESC LIMIT 1',
                           (public_key,self.clock())).fetchone()
        if row is None or row['seen']+self.PEER_LEASE<=self.clock():
            return None
        return {'room':row['room'],'actor':row['actor'],'device':row['device'],'expires':row['seen']+self.PEER_LEASE}

    def keys(self,ident):
        """Public keys of every member of a room (for immediate data-plane revocation)."""
        with self._tx() as db:
            return [r[0] for r in db.execute('SELECT public_key FROM peers WHERE room=?',(ident,))]

    def require_device(self,actor,ident,device_id):
        with self._tx() as db:
            peer=self._member(db,self._room(db,ident),actor)
            if not device_id or peer['device']!=device_id:
                raise RoomError('Membership belongs to another device.')

    def heartbeat(self,actor,ident):
        with self._tx() as db:
            room=self._room(db,ident)
            self._member(db,room,actor)
            db.execute('UPDATE peers SET seen=? WHERE room=? AND actor=?',(self.clock(),ident,actor))

    def rotate_invite(self,actor,ident):
        invite=secrets.token_urlsafe(24)
        with self._tx() as db:
            self._owner(self._room(db,ident),actor)
            db.execute('UPDATE rooms SET invite=?,invite_expiry=? WHERE id=?',
                       (self._hash(invite),self.clock()+self.INVITE_TTL,ident))
        return invite

    def remove(self,actor,ident,member):
        with self._tx() as db:
            room=self._room(db,ident)
            self._owner(room,actor)
            if member==actor:
                raise RoomError('The owner must delete the room instead.')
            self._actor(member)
            db.execute('INSERT OR IGNORE INTO banned VALUES (?,?)',(ident,member))
            db.execute('DELETE FROM peers WHERE room=? AND actor=?',(ident,member))

    def leave(self,actor,ident):
        with self._tx() as db:
            room=self._room(db,ident)
            self._member(db,room,actor)
            if room['owner']==actor:
                raise RoomError('The owner must delete the room instead.')
            db.execute('DELETE FROM peers WHERE room=? AND actor=?',(ident,actor))

    def delete(self,actor,ident):
        with self._tx() as db:
            self._owner(self._room(db,ident),actor)
            db.execute('DELETE FROM rooms WHERE id=?',(ident,))
