"""Transactional LAN-room coordination prototype, for isolated tests and later authenticated API integration.

`actor` MUST come from the existing authenticated and licensed server session, never a request body. This
module exposes no endpoint and is not imported by the production app. It creates no adapter, tunnel, relay,
firewall rule or Internet exit. Virtual addresses below are allocations, not evidence of connectivity.
"""
from __future__ import annotations

import base64
import hashlib
import ipaddress
import re
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
    MAX_PUBLIC_ROOMS = 1
    REPORT_TTL = 7 * 86400
    REGIONS = frozenset({'eu', 'na', 'sa', 'as', 'oc', 'af', 'me'})

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
            columns = {r[1] for r in db.execute('PRAGMA table_info(rooms)')}
            for column, declaration in (
                ('visibility', "TEXT NOT NULL DEFAULT 'private'"),
                ('game_id', "TEXT NOT NULL DEFAULT ''"), ('region', "TEXT NOT NULL DEFAULT ''"),
                ('capacity', 'INTEGER NOT NULL DEFAULT 16'), ('locked', 'INTEGER NOT NULL DEFAULT 0'),
            ):
                if column not in columns:
                    db.execute(f'ALTER TABLE rooms ADD COLUMN {column} {declaration}')
            db.executescript('''
                CREATE INDEX IF NOT EXISTS rooms_browser ON rooms(visibility,game_id,region,expiry);
                CREATE TABLE IF NOT EXISTS room_reports (
                    id TEXT PRIMARY KEY, room TEXT NOT NULL, reporter TEXT NOT NULL, category TEXT NOT NULL,
                    created REAL NOT NULL, UNIQUE(room,reporter));
                CREATE INDEX IF NOT EXISTS reports_age ON room_reports(created);
            ''')

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

    @classmethod
    def _metadata(cls, visibility, game_id, region, capacity):
        if visibility not in ('private', 'public'):
            raise RoomError('Invalid room visibility.')
        if not isinstance(game_id, str) or not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,63}|', game_id):
            raise RoomError('Invalid game identity.')
        if region not in cls.REGIONS and region != '':
            raise RoomError('Invalid room region.')
        if type(capacity) is not int or not 2 <= capacity <= cls.MAX_PEERS:
            raise RoomError('Invalid room capacity.')
        if visibility == 'public' and (not game_id or not region):
            raise RoomError('Public rooms require a game and region.')

    def _public_quota(self, db, actor, excluding=''):
        if db.execute("SELECT count(*) FROM rooms WHERE owner=? AND visibility='public' AND expiry>? AND id!=?",
                      (actor, self.clock(), excluding)).fetchone()[0] >= self.MAX_PUBLIC_ROOMS:
            raise RoomError('Public room limit reached.')

    def create(self,actor,name,public_key,password='',device_id='',*,visibility='private',game_id='',region='',capacity=16):
        self._actor(actor)
        self._key(public_key)
        self._metadata(visibility, game_id, region, capacity)
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
            if visibility == 'public':
                self._public_quota(db, actor)
            slots={r[0] for r in db.execute('SELECT slot FROM rooms')}
            slot=next((n for n in range(1,255) if n not in slots),None)
            if slot is None:
                raise RoomError('No room capacity.')
            db.execute('INSERT INTO rooms (id,owner,name,slot,password,invite,invite_expiry,expiry,visibility,game_id,region,capacity) '
                       'VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                       (ident,actor,name.strip(),slot,password_hash,self._hash(invite),now+self.INVITE_TTL,
                        now+self.ROOM_TTL,visibility,game_id,region,capacity))
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
            return self._join(db, room, actor, public_key, password, device_id)

    def _join(self, db, room, actor, public_key, password, device_id):
        ident, now = room['id'], self.clock()
        if db.execute('SELECT 1 FROM banned WHERE room=? AND actor=?',(ident,actor)).fetchone():
            raise RoomError('Membership revoked.')
        existing=db.execute('SELECT * FROM peers WHERE room=? AND actor=?',(ident,actor)).fetchone()
        if existing:
            if existing['public_key']!=public_key or existing['device']!=device_id:
                raise RoomError('Leave the room before registering another public key.')
            db.execute('UPDATE peers SET seen=? WHERE room=? AND actor=?',(now,ident,actor))
            return ident
        if room['locked']:
            raise RoomError('Room is locked.')
        if room['password']:
            try:
                self.passwords.verify(room['password'],password)
            except VerificationError:
                raise RoomError('Invalid room password.') from None
        hosts={p[0] for p in db.execute('SELECT host FROM peers WHERE room=?',(ident,))}
        if len(hosts)>=room['capacity']:
            raise RoomError('Room is full.')
        host=next(n for n in range(2,255) if n not in hosts)
        try:
            db.execute('INSERT INTO peers VALUES (?,?,?,?,?,?)',(ident,actor,public_key,host,now,device_id))
        except sqlite3.IntegrityError:
            raise RoomError('Public key is already registered.') from None
        return ident

    def join_public(self, actor, ident, public_key, password='', device_id=''):
        self._actor(actor)
        self._key(public_key)
        if not isinstance(password,str) or len(password)>128:
            raise RoomError('Invalid room password.')
        with self._tx() as db:
            room = self._room(db, ident)
            if room['visibility'] != 'public':
                raise RoomError('Room is unavailable.')
            owner = db.execute('SELECT seen FROM peers WHERE room=? AND actor=?', (ident,room['owner'])).fetchone()
            if owner is None or owner['seen'] + self.PEER_LEASE <= self.clock():
                raise RoomError('Room is unavailable.')
            return self._join(db, room, actor, public_key, password, device_id)

    def publish(self, actor, ident, *, visibility, game_id, region, capacity, locked=False):
        self._metadata(visibility, game_id, region, capacity)
        if type(locked) is not bool:
            raise RoomError('Invalid room lock.')
        with self._tx() as db:
            room = self._room(db, ident)
            self._owner(room, actor)
            if db.execute('SELECT count(*) FROM peers WHERE room=?',(ident,)).fetchone()[0] > capacity:
                raise RoomError('Room capacity is below its current membership.')
            if visibility == 'public':
                self._public_quota(db, actor, ident)
            db.execute('UPDATE rooms SET visibility=?,game_id=?,region=?,capacity=?,locked=? WHERE id=?',
                       (visibility, game_id, region, capacity, int(locked), ident))

    def browse(self, *, search='', game_id='', region='', minimum_players=0, open_only=True, limit=20, offset=0):
        if not isinstance(search,str) or len(search)>64 or any(ord(c)<32 for c in search):
            raise RoomError('Invalid room search.')
        self._metadata('private', game_id, region, self.MAX_PEERS)
        if type(minimum_players) is not int or not 0 <= minimum_players <= self.MAX_PEERS or \
                type(limit) is not int or not 1 <= limit <= 50 or type(offset) is not int or not 0 <= offset <= 1000 \
                or type(open_only) is not bool:
            raise RoomError('Invalid browser limits.')
        now = self.clock()
        with self._tx() as db:
            db.execute('DELETE FROM rooms WHERE expiry<=?', (now,))
            # Browser fields are deliberately enumerated; never return accounts, keys, invite, addresses or slots.
            rows = db.execute("SELECT r.*, (SELECT count(*) FROM peers p WHERE p.room=r.id AND p.seen>?) AS count, "
                              "(SELECT count(*) FROM peers p WHERE p.room=r.id) AS reserved FROM rooms r "
                              "WHERE visibility='public' AND expiry>? AND EXISTS "
                              "(SELECT 1 FROM peers o WHERE o.room=r.id AND o.actor=r.owner AND o.seen>?) "
                              "ORDER BY r.expiry,r.id", (now-self.PEER_LEASE,now,now-self.PEER_LEASE)).fetchall()
            items=[]
            for row in rows:
                if game_id and row['game_id'] != game_id or region and row['region'] != region \
                        or search.casefold() not in row['name'].casefold() or row['count'] < minimum_players:
                    continue
                status = 'locked' if row['locked'] else 'full' if row['reserved'] >= row['capacity'] else 'open'
                if open_only and status != 'open':
                    continue
                items.append({'room_id':row['id'],'name':row['name'],'game_id':row['game_id'],'region':row['region'],
                              'players':row['count'],'capacity':row['capacity'],'password_required':bool(row['password']),
                              'status':status,'visibility':'public','transport_policy':'relay_only','latency_ms':None})
            return {'rooms':items[offset:offset+limit], 'offset':offset,'limit':limit,
                    'next_offset':offset+limit if len(items)>offset+limit else None}

    def report(self, actor, ident, category):
        self._actor(actor)
        if category not in ('spam','harassment','unsafe_content','other'):
            raise RoomError('Invalid report category.')
        with self._tx() as db:
            room = self._room(db, ident)
            if room['visibility'] != 'public':
                self._member(db, room, actor)
            now = self.clock()
            db.execute('DELETE FROM room_reports WHERE created<=?',(now-self.REPORT_TTL,))
            if db.execute('SELECT 1 FROM room_reports WHERE room=? AND reporter=?',(ident,actor)).fetchone():
                return
            if db.execute('SELECT count(*) FROM room_reports WHERE reporter=? AND created>?',
                          (actor,now-86400)).fetchone()[0] >= 10:
                raise RoomError('Report limit reached.')
            if db.execute('SELECT count(*) FROM room_reports').fetchone()[0] >= 1000:
                raise RoomError('Report queue is full.')
            db.execute('INSERT INTO room_reports VALUES (?,?,?,?,?)',
                       (secrets.token_hex(16),ident,actor,category,now))

    def members(self,actor,ident):
        with self._tx() as db:
            room=self._room(db,ident)
            self._member(db,room,actor)
            return [{'actor':r['actor'],'device_id':r['device'],'public_key':r['public_key'],
                     'virtual_ip':str(ipaddress.ip_address(f"10.203.{room['slot']}.{r['host']}")),
                     'status':'registered' if r['seen']+self.PEER_LEASE>self.clock() else 'expired',
                     'owner':r['actor']==room['owner'],'transport':'not_implemented'}
                    for r in db.execute('SELECT * FROM peers WHERE room=? ORDER BY host',(ident,))]

    def info(self,actor,ident):
        """Room facts a member may see: its name, whether the caller owns it and when it expires."""
        with self._tx() as db:
            room=self._room(db,ident)
            self._member(db,room,actor)
            return {'name':room['name'],'owner':room['owner']==actor,'expires_at':int(room['expiry']),
                    'password':bool(room['password']),'visibility':room['visibility'],'game_id':room['game_id'],
                    'region':room['region'],'capacity':room['capacity'],'locked':bool(room['locked']),
                    'transport_policy':'relay_only'}

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
