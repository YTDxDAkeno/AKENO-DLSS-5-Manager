"""Authenticated public LAN-room browser. No private invites, network endpoints, account IDs or keys are listed."""
from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import Field
from sqlalchemy.orm import Session

from akeno_server.api.deps import Caller, current, limit, not_in_maintenance
from akeno_server.db import get_db
from akeno_server.errors import forbidden
from akeno_server.network.api import DeviceBody, authorised, bound, operation

router = APIRouter(prefix='/api/v1/network/lan/browser', tags=['lan-browser'],
                   dependencies=[Depends(not_in_maintenance)])


class PublishRoom(DeviceBody):
    visibility: Literal['private','public'] = 'public'
    game_id: str = Field(min_length=1, max_length=64, pattern=r'^[a-z0-9][a-z0-9_-]*$')
    region: Literal['eu','na','sa','as','oc','af','me']
    capacity: int = Field(default=16, ge=2, le=16)
    locked: bool = False


class CreatePublicRoom(PublishRoom):
    name: str = Field(min_length=1, max_length=64)
    public_key: str = Field(min_length=44, max_length=44)
    password: str = Field(default='', max_length=128, repr=False)


class JoinPublicRoom(DeviceBody):
    public_key: str = Field(min_length=44, max_length=44)
    password: str = Field(default='', max_length=128, repr=False)


class ReportRoom(DeviceBody):
    category: Literal['spam','harassment','unsafe_content','other']


def game(ident):
    # One shared profile format for API and client. The server package also supports standalone deployments,
    # therefore the reviewed IDs below are deliberately independent from the desktop Qt package.
    if ident not in ('openttd','factorio','terraria'):
        raise forbidden('game_profile_unavailable','This game has no reviewed LAN port profile.')


@router.get('/rooms')
def browse(request:Request, device_id:uuid.UUID, search:str=Query(default='',max_length=64),
           game_id:str=Query(default='',max_length=64), region:str=Query(default='',max_length=2),
           minimum_players:int=Query(default=0,ge=0,le=16), open_only:bool=True,
           limit_:int=Query(default=20,alias='limit',ge=1,le=50), offset:int=Query(default=0,ge=0,le=1000),
           caller:Caller=Depends(current), db:Session=Depends(get_db)):
    actor,device,s = authorised(request,device_id,caller,db)
    return operation(lambda:s.browse(search=search,game_id=game_id,region=region,minimum_players=minimum_players,
                                    open_only=open_only,limit=limit_,offset=offset))


@router.post('/rooms', status_code=201)
def create(body:CreatePublicRoom, request:Request, caller:Caller=Depends(current), db:Session=Depends(get_db)):
    actor,device,s = authorised(request,body.device_id,caller,db)
    limit(request,'lan_join',actor)
    game(body.game_id)
    result = operation(lambda:s.create(actor,body.name,body.public_key,body.password,device,
                                      visibility=body.visibility,game_id=body.game_id,region=body.region,
                                      capacity=body.capacity))
    if body.locked:
        operation(lambda:s.publish(actor,result['room_id'],visibility=body.visibility,game_id=body.game_id,
                                   region=body.region,capacity=body.capacity,locked=True))
    return result


@router.patch('/rooms/{room}')
def publish(room:str, body:PublishRoom, request:Request, caller:Caller=Depends(current), db:Session=Depends(get_db)):
    actor,device,s = bound(request,room,body.device_id,caller,db)
    game(body.game_id)
    operation(lambda:s.publish(actor,room,visibility=body.visibility,game_id=body.game_id,
                               region=body.region,capacity=body.capacity,locked=body.locked))
    return {'room_id':room,'visibility':body.visibility,'locked':body.locked,'transport_policy':'relay_only'}


@router.post('/rooms/{room}/join')
def join(room:str, body:JoinPublicRoom, request:Request, caller:Caller=Depends(current), db:Session=Depends(get_db)):
    actor,device,s = authorised(request,body.device_id,caller,db)
    limit(request,'lan_join',actor)
    return {'room_id':operation(lambda:s.join_public(actor,room,body.public_key,body.password,device)),
            'transport_policy':'relay_only','transport':'not_implemented'}


@router.post('/rooms/{room}/reports', status_code=202)
def report(room:str, body:ReportRoom, request:Request, caller:Caller=Depends(current), db:Session=Depends(get_db)):
    actor,device,s = authorised(request,body.device_id,caller,db)
    operation(lambda:s.report(actor,room,body.category))
    return {'accepted':True}
