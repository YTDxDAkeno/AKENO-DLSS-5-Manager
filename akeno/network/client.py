"""LAN client shares AKENO's existing sign-in, refresh and device identity. Only public tunnel keys leave the PC."""
import base64
import uuid
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives import serialization
from akeno.network.credentials import verify_peer


class LanClient:
    def __init__(self,license_manager,identity_store):
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
        with self.license._lock:
            return self.license._authed(self.license._api(),method,'/network/lan/'+path,body)

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
        keys=self.license.trusted_keys()
        claims=verify_peer(result['credential'],keys,room)
        if claims['device_id']!=i['device_id'] or claims['public_key']!=i['public_key']:
            raise ValueError('Peer credential does not match this PC.')
        return result

    def leave(self,room):
        i=self._identity()
        result=self._call('POST','rooms/'+self._room(room)+'/leave',{'device_id':i['device_id']})
        i.pop('room_id',None);self.identity.save(i)
        return result
