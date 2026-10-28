"""Strict verification of short-lived, room-bound server credentials; never a license entitlement."""
import ipaddress
import base64
import re
import time
from akeno.licensing.crypto import verify, kid_for, public_key


def validate_relay(value):
    """The controlled relay's signed address, never a member's home endpoint."""
    if value is None:return None
    try:
        if not isinstance(value,dict) or set(value)!={'public_key','host','port'}:
            raise ValueError()
        key=value['public_key'];host=value['host'];port=value['port']
        raw=base64.b64decode(key,validate=True)
        if len(raw)!=32 or base64.b64encode(raw).decode()!=key or type(port) is not int or not 1<=port<=65535:
            raise ValueError()
        if not isinstance(host,str) or not 1<=len(host)<=253:
            raise ValueError()
        try:ipaddress.ip_address(host)
        except ValueError:
            labels=host.rstrip('.').split('.')
            if not all(re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?',label) for label in labels):
                raise ValueError()
        return dict(value)
    except Exception:
        raise ValueError('Invalid signed relay information.') from None


def verify_network_keys(document, account_keys, now=None, purpose='akeno-lan-peer'):
    """Accept a network signing key only through a fresh, typed delegation from an existing account trust anchor.

    A bare HTTPS JSON key, a release key or a license document is insufficient. The returned keys are used only
    for the specified network purpose and must never be added to LicenseManager's general trusted-key set.
    """
    now=time.time() if now is None else now
    try:
        if purpose not in ('akeno-lan-peer','akeno-vpn-tunnel') or not isinstance(document,dict):
            raise ValueError()
        token=document['credential']
        if not isinstance(token,str) or len(token)>8192:
            raise ValueError()
        _,p=verify(token,account_keys,'akeno-network-key-delegation')
        if p['iss']!='akeno-network' or p['aud']!='akeno-network-key' or type(p['iat']) is not int \
                or type(p['exp']) is not int or not p['iat']<=now<p['exp'] or not 0<p['exp']-p['iat']<=3600:
            raise ValueError()
        purposes=p['purposes']
        if not isinstance(purposes,list) or purpose not in purposes or \
                any(item not in ('akeno-lan-peer','akeno-vpn-tunnel') for item in purposes):
            raise ValueError()
        keys=p['keys']
        if not isinstance(keys,dict) or len(keys)!=1 or document.get('keys')!=keys:
            raise ValueError()
        trusted={}
        for kid,value in keys.items():
            if not isinstance(kid,str) or not isinstance(value,str) or kid_for(value)!=kid or kid in account_keys:
                raise ValueError()
            trusted[kid]=public_key(value)
        return trusted
    except Exception:
        raise ValueError('Network signing delegation is invalid, expired or has the wrong purpose.') from None


def verify_peer(token,keys,room,now=None):
    now=time.time() if now is None else now
    if not isinstance(token,str) or len(token)>8192:
        raise ValueError('Invalid peer credential.')
    try:
        _,p=verify(token,keys,'akeno-lan-peer')
        if set(p)!={'iss','aud','sub','device_id','room_id','public_key','virtual_ip','iat','exp','nonce','transport','relay'}:
            raise ValueError()
        if p['iss']!='akeno-lan' or p['aud']!='akeno-lan-peer' or p['room_id']!=room \
                or type(p['iat']) is not int or type(p['exp']) is not int or \
                not p['iat']<=now<p['exp'] or not 0<p['exp']-p['iat']<=45:
            raise ValueError()
        if p.get('transport') != 'relay-only' or 'endpoint' in p:
            raise ValueError()
        validate_relay(p['relay'])
        import base64
        if len(base64.b64decode(p['public_key'],validate=True))!=32:
            raise ValueError()
        addr=ipaddress.ip_address(p['virtual_ip'])
        if addr.version!=4 or addr not in ipaddress.ip_network('10.203.0.0/16') or int(str(addr).rsplit('.',1)[1]) not in range(2,255):
            raise ValueError()
        import uuid
        uuid.UUID(p['sub']);uuid.UUID(p['device_id'])
        if not isinstance(p['nonce'],str) or len(p['nonce'])<16:raise ValueError()
        return p
    except Exception:
        raise ValueError('Peer credential is invalid, expired or belongs to another room.') from None
