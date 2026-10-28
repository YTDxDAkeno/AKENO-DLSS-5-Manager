"""LAN client shares AKENO's existing sign-in, refresh and device identity. Only public tunnel keys leave the PC."""
import base64
import uuid
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives import serialization
from akeno.network.credentials import verify_peer, verify_network_keys


class LanUnavailable(Exception):
    """The AKENO server this build talks to does not offer LAN Play (an older server, or the operator has not enabled
    it yet). Raised instead of the server's bare 404 so the UI can say so instead of showing "Not Found"."""

    def __init__(self):
        super().__init__('LAN Play is not available on the AKENO server yet. Nothing was changed on this PC.')


def _missing_route(exc):
    from akeno.licensing.api import ApiError
    return isinstance(exc,ApiError) and exc.status in (404,405) and exc.code in ('not_found','method_not_allowed')


class LanClient:
    def __init__(self,license_manager,identity_store):
        from akeno.licensing.storage import SecureStore
        if isinstance(identity_store,SecureStore) and not identity_store.protected:
            raise ValueError('LAN identity storage requires Windows DPAPI; no plaintext fallback is permitted.')
        self.license,self.identity=license_manager,identity_store

    def _identity(self):
        device=self.license.state.get('device_id')
        if not device:raise ValueError('Register this PC with your AKENO account first.')
        uuid.UUID(device)
        state=self.identity.load()
        if state.get('device_id')!=device:
            key=X25519PrivateKey.generate()
            state={'device_id':device,'private_key':base64.b64encode(key.private_bytes(serialization.Encoding.Raw,
                   serialization.PrivateFormat.Raw,serialization.NoEncryption())).decode(),
                   'public_key':base64.b64encode(key.public_key().public_bytes(serialization.Encoding.Raw,
                                   serialization.PublicFormat.Raw)).decode()}
            self.identity.save(state)
        return state

    def _call(self,method,path,body=None):
        try:
            with self.license._lock:
                return self.license._authed(self.license._api(),method,'/network/lan/'+path,body)
        except Exception as exc:
            # The LAN router never answers 404/405 itself (room problems are 403), so these mean "no such API here".
            if _missing_route(exc):raise LanUnavailable() from exc
            raise

    def status(self):
        """Unauthenticated capability probe: {'enabled': bool, 'transport': ..., 'relay': bool}. Never raises for a
        server without LAN Play; raises the licensing errors (offline, certificate, ...) unchanged."""
        try:
            result=self.license._api().call('GET','/network/lan/status')
        except Exception as exc:
            if _missing_route(exc):return {'enabled':False,'transport':'not_implemented','relay':False}
            raise
        return {'enabled':result.get('enabled') is True,'transport':result.get('transport','not_implemented'),
                'relay':result.get('relay') is True}

    def create(self,name,password=''):
        i=self._identity()
        result=self._call('POST','rooms',{'device_id':i['device_id'],'public_key':i['public_key'],'name':name,'password':password})
        self.identity.save({**i,'room_id':result['room_id']})
        return result

    def join(self,invite,password=''):
        i=self._identity()
        result=self._call('POST','join',{'device_id':i['device_id'],'public_key':i['public_key'],'invite':invite,'password':password})
        self.identity.save({**i,'room_id':result['room_id']})
        return result

    @staticmethod
    def _room(room):
        if not isinstance(room,str) or len(room)!=32 or any(c not in '0123456789abcdef' for c in room):
            raise ValueError('Invalid room identity.')
        return room

    def heartbeat(self,room):
        i=self._identity();room=self._room(room)
        result=self._call('POST','rooms/'+room+'/heartbeat',{'device_id':i['device_id']})
        keys=verify_network_keys(result.get('network_keys'),self.license.trusted_keys())
        claims=verify_peer(result['credential'],keys,room)
        if claims['device_id']!=i['device_id'] or claims['public_key']!=i['public_key']:
            raise ValueError('Peer credential does not match this PC.')
        if claims['relay']!=result.get('coordinator') or result.get('relay_only') is not True:
            raise ValueError('LAN relay information does not match the signed authorization.')
        return result

    def leave(self,room):
        i=self._identity()
        result=self._call('POST','rooms/'+self._room(room)+'/leave',{'device_id':i['device_id']})
        i.pop('room_id',None);self.identity.save(i)
        return result

    def rotate_invite(self,room):
        i=self._identity()
        return self._call('POST','rooms/'+self._room(room)+'/invite',{'device_id':i['device_id']})

    def remove_member(self,room,member):
        i=self._identity();member=str(uuid.UUID(member))
        return self._call('DELETE','rooms/'+self._room(room)+'/members/'+member+'?device_id='+i['device_id'])

    def delete(self,room):
        i=self._identity()
        result=self._call('DELETE','rooms/'+self._room(room)+'?device_id='+i['device_id'])
        i.pop('room_id',None);self.identity.save(i)
        return result
