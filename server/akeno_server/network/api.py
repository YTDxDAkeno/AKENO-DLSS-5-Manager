"""Opt-in LAN control plane. Uses existing sessions/entitlements/devices; exposes no tunnel management shell."""
from __future__ import annotations
import secrets
import uuid
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from akeno_server.api.deps import Caller, current, limit, not_in_maintenance
from akeno_server.clock import utcnow
from akeno_server.config import get_config
from akeno_server.db import get_db
from akeno_server.errors import ApiError, forbidden, unavailable
from akeno_server.licensing import service as licenses, signing
from akeno_server.models import Device, User
from akeno_server.network.rooms import RoomError

router=APIRouter(prefix='/api/v1/network/lan',tags=['lan-play'],dependencies=[Depends(not_in_maintenance)])


class DeviceBody(BaseModel):
    model_config=ConfigDict(extra='forbid')
    device_id: uuid.UUID


class CreateRoom(DeviceBody):
    name: str=Field(min_length=1,max_length=64)
    public_key: str=Field(min_length=44,max_length=44)
    password: str=Field(default='',max_length=128,repr=False)


class JoinRoom(DeviceBody):
    invite: str=Field(min_length=16,max_length=128,repr=False)
    public_key: str=Field(min_length=44,max_length=44)
    password: str=Field(default='',max_length=128,repr=False)


def authorised(request:Request,device_id:uuid.UUID,caller:Caller,db:Session):
    limit(request,'lan_ip')
    limit(request,'lan_user',str(caller.user.id))
    if caller.user.email_verified_at is None:
        raise forbidden('email_not_verified','Confirm your email before using LAN Play.')
    license_=licenses.license_of(db,caller.user.id)
    if license_ is None or not license_.running(utcnow()):
        raise forbidden('active_license_required','An active AKENO suite license is required.')
    device=db.get(Device,device_id)
    if device is None or device.user_id!=caller.user.id or not device.active or caller.session.device_id!=device.id:
        raise forbidden('device_not_authorised','Register and use the device bound to this session.')
    return str(caller.user.id),str(device.id),request.app.state.lan_rooms


def operation(fn):
    try:
        return fn()
    except RoomError as exc:
        raise forbidden('room_access_denied',str(exc)) from None


def bound(request,room,device_id,caller,db):
    actor,device,s=authorised(request,device_id,caller,db)
    operation(lambda:s.require_device(actor,room,device))
    return actor,device,s


def members(actor,room,s,db):
    # Revoked accounts/devices or expired entitlements are not supplied as usable peers.
    valid=[]
    for peer in operation(lambda:s.members(actor,room)):
        try:
            uid,did=uuid.UUID(peer['actor']),uuid.UUID(peer['device_id'])
        except ValueError:
            continue
        user,device=db.get(User,uid),db.get(Device,did)
        license_=licenses.license_of(db,uid)
        if user and user.active and device and device.active and device.user_id==uid and \
                license_ and license_.running(utcnow()) and peer['status']=='registered':
            valid.append(peer)
    return valid


@router.get('/status')
def status(request:Request):
    """Capability probe for clients. The whole router exists only when the operator enabled LAN Play, so an older or
    disabled server answers 404 here and clients show "not available" instead of failing on their first action."""
    limit(request,'lan_ip')
    c=coordinator(request)
    wireguard=c is not None and bool(get_config().network_coordinator_host)
    return {'enabled':True,'api':1,'transport':'wireguard' if wireguard else 'not_implemented','relay':wireguard}


@router.post('/rooms',status_code=201)
def create(body:CreateRoom,request:Request,caller:Caller=Depends(current),db:Session=Depends(get_db)):
    actor,device,s=authorised(request,body.device_id,caller,db)
    return operation(lambda:s.create(actor,body.name,body.public_key,body.password,device))


@router.post('/join')
def join(body:JoinRoom,request:Request,caller:Caller=Depends(current),db:Session=Depends(get_db)):
    actor,device,s=authorised(request,body.device_id,caller,db)
    limit(request,'lan_join',actor)
    return {'room_id':operation(lambda:s.join(actor,body.invite,body.public_key,body.password,device)),
            'transport':'not_implemented'}


@router.get('/rooms/{room}/members')
def list_members(room:str,device_id:uuid.UUID,request:Request,caller:Caller=Depends(current),db:Session=Depends(get_db)):
    actor,device,s=bound(request,room,device_id,caller,db)
    return {'members':members(actor,room,s,db),'lease_seconds':s.PEER_LEASE,'transport':'not_implemented'}


def coordinator(request):
    return getattr(request.app.state,'lan_coordinator',None)


def revoke(request,keys):
    """Data-plane effect of a removal, leave or deletion: the relay stops at once, not at the next lease check."""
    c=coordinator(request)
    for key in keys:
        if c is not None:c.revoke(key)


def credential(room,peer,lease,c):
    """A short-lived signed binding of one member: room, account, device, WireGuard key, virtual address and the
    endpoint the coordinator proved for that key (or None). Clients configure peers only from these."""
    now=int(utcnow().timestamp())
    endpoint=c.endpoint(peer['public_key']) if c is not None else None
    payload={'iss':'akeno-lan','aud':'akeno-lan-peer','sub':peer['actor'],'device_id':peer['device_id'],'room_id':room,
             'public_key':peer['public_key'],'virtual_ip':peer['virtual_ip'],'iat':now,'exp':now+lease,
             'nonce':secrets.token_urlsafe(16),'endpoint':list(endpoint) if endpoint else None}
    try:
        kid,key=signing.store().active()
        return signing.sign(key,kid,'akeno-lan-peer',payload),payload
    except signing.SigningUnavailable:
        raise unavailable('peer_signing_unavailable','Peer credentials cannot be issued.') from None


@router.post('/rooms/{room}/heartbeat')
def heartbeat(room:str,body:DeviceBody,request:Request,caller:Caller=Depends(current),db:Session=Depends(get_db)):
    actor,device,s=bound(request,room,body.device_id,caller,db)
    operation(lambda:s.heartbeat(actor,room))
    c=coordinator(request)
    peer=next(p for p in operation(lambda:s.members(actor,room)) if p['actor']==actor)
    ticket,payload=credential(room,peer,s.PEER_LEASE,c)
    valid=members(actor,room,s,db)
    host=get_config().network_coordinator_host
    info={'public_key':c.public_key,'host':host,'port':c.address[1]} if c is not None and host else None
    return {'credential':ticket,'expires_at':payload['exp'],'members':valid,
            'peers':[{'credential':credential(room,p,s.PEER_LEASE,c)[0]} for p in valid if p['actor']!=actor],
            'coordinator':info,'transport':'wireguard' if info else 'not_implemented'}


@router.post('/rooms/{room}/invite')
def invite(room:str,body:DeviceBody,request:Request,caller:Caller=Depends(current),db:Session=Depends(get_db)):
    actor,device,s=bound(request,room,body.device_id,caller,db)
    return {'invite':operation(lambda:s.rotate_invite(actor,room)),'expires_at':int(s.clock())+s.INVITE_TTL}


@router.delete('/rooms/{room}/members/{member}',status_code=204)
def remove(room:str,member:uuid.UUID,device_id:uuid.UUID,request:Request,caller:Caller=Depends(current),db:Session=Depends(get_db)):
    actor,device,s=bound(request,room,device_id,caller,db)
    keys=[p['public_key'] for p in operation(lambda:s.members(actor,room)) if p['actor']==str(member)]
    operation(lambda:s.remove(actor,room,str(member)))
    revoke(request,keys)


@router.post('/rooms/{room}/leave',status_code=204)
def leave(room:str,body:DeviceBody,request:Request,caller:Caller=Depends(current),db:Session=Depends(get_db)):
    actor,device,s=bound(request,room,body.device_id,caller,db)
    keys=[p['public_key'] for p in operation(lambda:s.members(actor,room)) if p['actor']==actor]
    operation(lambda:s.leave(actor,room))
    revoke(request,keys)


@router.delete('/rooms/{room}',status_code=204)
def delete(room:str,device_id:uuid.UUID,request:Request,caller:Caller=Depends(current),db:Session=Depends(get_db)):
    actor,device,s=bound(request,room,device_id,caller,db)
    keys=operation(lambda:s.keys(room))
    operation(lambda:s.delete(actor,room))
    revoke(request,keys)
