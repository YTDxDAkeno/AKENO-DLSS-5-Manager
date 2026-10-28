"""Opt-in VPN API, sharing existing account sessions, suite entitlements and exact registered devices.

Operator credentials, exit-agent credentials, network signing keys and independent probe keys have separate
purposes. No public server is registered, enabled, contacted or provisioned by importing this module.
"""
from __future__ import annotations

import hmac
import secrets
import uuid

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from akeno_server.api.deps import Caller, current, limit, not_in_maintenance
from akeno_server.clock import utcnow
from akeno_server.config import get_config
from akeno_server.db import get_db
from akeno_server.errors import forbidden, bad_request, unavailable
from akeno_server.licensing import service as licenses
from akeno_server.models import Device, User
from akeno_server.network import signing
from akeno_server.vpn.proofs import verify_proof
from akeno_server.vpn.store import VPNError

router = APIRouter(prefix='/api/v1/network/vpn', tags=['vpn'], dependencies=[Depends(not_in_maintenance)])


class StrictBody(BaseModel):
    model_config = ConfigDict(extra='forbid')


class DeviceBody(StrictBody):
    device_id: uuid.UUID


class Provision(DeviceBody):
    public_key: str = Field(min_length=44, max_length=44)
    country: str = Field(pattern='^[A-Z]{2}$')
    request_id: uuid.UUID


class Registration(StrictBody):
    name: str = Field(min_length=1, max_length=64)
    country: str = Field(pattern='^[A-Z]{2}$')
    public_key: str = Field(min_length=44, max_length=44)
    endpoint: str = Field(min_length=3, max_length=100)
    subnet4: str = Field(min_length=8, max_length=32)
    subnet6: str = Field(min_length=8, max_length=64)
    dns: list[str] = Field(min_length=1, max_length=4)
    capacity: int = Field(default=100, ge=1, le=250, strict=True)


class ProofBody(StrictBody):
    proof: str = Field(max_length=8192, repr=False)


class ProbeLease(StrictBody):
    public_key: str = Field(min_length=44, max_length=44)


class AgentHeartbeat(StrictBody):
    public_key: str = Field(min_length=44, max_length=44)
    generation: int = Field(ge=0, strict=True)


def store(request):
    return request.app.state.vpn_store


def operation(fn):
    try:
        return fn()
    except VPNError as exc:
        raise bad_request('vpn_request_denied', str(exc)) from None


def authenticated(request, device_id, caller, db, *, cleanup=False):
    limit(request, 'vpn_ip'); limit(request, 'vpn_user', str(caller.user.id))
    device = db.get(Device, device_id)
    if device is None or device.user_id != caller.user.id or not device.active or caller.session.device_id != device.id:
        raise forbidden('device_not_authorised', 'Register and use the device bound to this session.')
    if not cleanup:
        if caller.user.email_verified_at is None:
            raise forbidden('email_not_verified', 'Confirm your email before using AKENO VPN.')
        license_ = licenses.license_of(db, caller.user.id)
        if license_ is None or not license_.running(utcnow()):
            raise forbidden('active_license_required', 'An active AKENO suite license is required.')
    return str(caller.user.id), str(device.id)


def _tls(request):
    if get_config().environment == 'production' and request.url.scheme != 'https':
        raise forbidden('secure_transport_required', 'Use the configured HTTPS control endpoint.')


def _bearer(request):
    header = request.headers.get('authorization', '')
    return header[7:] if header.startswith('Bearer ') else ''


def operator(request):
    _tls(request); limit(request, 'vpn_operator')
    configured = get_config().vpn_operator_token.get_secret_value()
    token = _bearer(request)
    if len(configured) < 48 or not token or not hmac.compare_digest(token.encode(), configured.encode()):
        raise forbidden('vpn_operator_required', 'A separate VPN operator credential is required.')


def agent(request, ident):
    _tls(request); limit(request, 'vpn_agent', ident)
    if not store(request).authenticate_agent(ident, _bearer(request)):
        raise forbidden('vpn_agent_required', 'The registered exit agent credential is required.')


def signed(lease):
    now = int(utcnow().timestamp())
    claims = {**lease, 'iss': 'akeno-vpn', 'aud': 'akeno-vpn-tunnel', 'sub': lease['actor'],
              'iat': now, 'exp': lease['expires_at'], 'nonce': secrets.token_urlsafe(24)}
    try:
        return {**lease, 'credential': signing.sign_token('akeno-vpn-tunnel', claims),
                'network_keys': signing.delegation()}
    except Exception:
        raise unavailable('vpn_signing_unavailable', 'VPN tunnel credentials cannot be issued.') from None


@router.get('/catalog')
def catalog(device_id: uuid.UUID, request: Request, caller: Caller = Depends(current), db: Session = Depends(get_db)):
    authenticated(request, device_id, caller, db)
    return store(request).catalog()


@router.post('/leases', status_code=201)
def provision(body: Provision, request: Request, caller: Caller = Depends(current), db: Session = Depends(get_db)):
    actor, device = authenticated(request, body.device_id, caller, db)
    return signed(operation(lambda: store(request).provision(actor, device, body.public_key, body.country, str(body.request_id))))


@router.get('/leases/{ident}')
def lease(ident: str, device_id: uuid.UUID, request: Request, caller: Caller = Depends(current), db: Session = Depends(get_db)):
    actor, device = authenticated(request, device_id, caller, db)
    return signed(operation(lambda: store(request).lease(ident, actor, device)))


@router.post('/leases/{ident}/renew')
def renew(ident: str, body: DeviceBody, request: Request, caller: Caller = Depends(current), db: Session = Depends(get_db)):
    actor, device = authenticated(request, body.device_id, caller, db)
    return signed(operation(lambda: store(request).renew(ident, actor, device)))


@router.delete('/leases/{ident}', status_code=204)
def disconnect(ident: str, device_id: uuid.UUID, request: Request, caller: Caller = Depends(current), db: Session = Depends(get_db)):
    actor, device = authenticated(request, device_id, caller, db, cleanup=True)
    operation(lambda: store(request).revoke(ident, actor, device))


@router.post('/operator/servers', status_code=201)
def register_exit(body: Registration, request: Request):
    operator(request)
    return operation(lambda: store(request).register(**body.model_dump()))


@router.post('/operator/servers/{ident}/verify')
def verify_exit(ident: str, body: ProofBody, request: Request):
    operator(request)
    s = store(request)
    with s._db() as db:
        row = operation(lambda: s._server(db, ident))
    claims = operation(lambda: verify_proof(body.proof, get_config().vpn_probe_keys_path, ident,
                                           row['public_key'], s.clock()))
    return operation(lambda: s.accept_proof(ident, claims))


@router.post('/operator/servers/{ident}/probe')
def provision_probe(ident: str, body: ProbeLease, request: Request):
    operator(request)
    return operation(lambda: store(request).provision_probe(ident, body.public_key))


@router.post('/operator/servers/{ident}/disable', status_code=204)
def disable_exit(ident: str, request: Request):
    operator(request); operation(lambda: store(request).disable(ident))


def _entitled(db, row):
    try:
        uid, did = uuid.UUID(row['actor']), uuid.UUID(row['device'])
    except ValueError:
        return False
    user, device = db.get(User, uid), db.get(Device, did)
    entitlement = licenses.license_of(db, uid)
    return bool(user and user.active and user.email_verified_at and device and device.active and device.user_id == uid
                and entitlement and entitlement.running(utcnow()))


@router.get('/agent/{ident}/peers')
def agent_peers(ident: str, request: Request, db: Session = Depends(get_db)):
    agent(request, ident)
    s = store(request)
    for row in s.active_leases(ident):
        if not _entitled(db, row):
            s.revoke(row['id'])
    return operation(lambda: s.manifest(ident))


@router.post('/agent/{ident}/heartbeat')
def agent_heartbeat(ident: str, body: AgentHeartbeat, request: Request):
    agent(request, ident)
    return operation(lambda: store(request).heartbeat(ident, body.public_key, body.generation))
