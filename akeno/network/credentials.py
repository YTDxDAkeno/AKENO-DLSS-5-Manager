"""Strict verification of short-lived, room-bound server credentials; never a license entitlement."""
import ipaddress
import time
from akeno.licensing.crypto import verify


def verify_peer(token,keys,room,now=None):
    now=time.time() if now is None else now
    if not isinstance(token,str) or len(token)>8192:
        raise ValueError('Invalid peer credential.')
    try:
        _,p=verify(token,keys,'akeno-lan-peer')
        if p['iss']!='akeno-lan' or p['aud']!='akeno-lan-peer' or p['room_id']!=room \
                or type(p['iat']) is not int or type(p['exp']) is not int or \
                not p['iat']<=now<p['exp'] or not 0<p['exp']-p['iat']<=45:
            raise ValueError()
        import base64
        if len(base64.b64decode(p['public_key'],validate=True))!=32:
            raise ValueError()
        addr=ipaddress.ip_address(p['virtual_ip'])
        if addr.version!=4 or addr not in ipaddress.ip_network('10.203.0.0/16'):
            raise ValueError()
        import uuid
        uuid.UUID(p['sub']);uuid.UUID(p['device_id'])
        if not isinstance(p['nonce'],str) or len(p['nonce'])<16:raise ValueError()
        return p
    except Exception:
        raise ValueError('Peer credential is invalid, expired or belongs to another room.') from None
