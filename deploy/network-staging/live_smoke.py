"""Actual local deployed staging smoke test. No production requests, no secret output, no OS tunnel/firewall."""
from pathlib import Path
import os,sys,json,time,copy,socket,base64,struct,urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo
SOURCE=Path('work/network-staging/source').resolve()
sys.path[:0]=[str(SOURCE),str(SOURCE/'server'),str(SOURCE/'server/tests')]
from akeno.licensing.api import LicenseApi,ApiError
from akeno.licensing.config import LicensingConfig
from akeno.licensing.manager import LicenseManager,UNLOCKED
from akeno.licensing import crypto
from akeno.network.client import LanClient
from akeno.network.credentials import verify_peer
from akeno_server.network import wireguard as wg
from wg_initiator import Member
OUT=Path('outputs/network-staging-smoke');BASE='http://127.0.0.1:18510';DEST=('127.0.0.1',3478)
RESULTS=[];CURRENT='initialization'

def check(name,passed):
 global CURRENT
 CURRENT=name
 if not passed:raise AssertionError('SMOKE_CHECK_FAILED')
 RESULTS.append({'check':name,'passed':True})

def refuses(fn,status):
 try:fn()
 except ApiError as e:return e.status==status
 return False

class MemoryStore:
 def __init__(self):self.value={}
 def load(self):return copy.deepcopy(self.value)
 def save(self,v):self.value=copy.deepcopy(v)

def api(manager,method,path,body=None):return manager._authed(manager._api(),method,path,body)
def udp():
 s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);s.bind(('127.0.0.1',0));s.settimeout(.8);return s

def silent(s,payload):
 s.sendto(payload,DEST)
 try:s.recvfrom(2048);return False
 except socket.timeout:return True

def main():
 global CURRENT
 for port in [18510,18511,18512]:
  with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/v1/system/health',timeout=5) as r:check('health_'+str(port),r.status==200)
 with urllib.request.urlopen(BASE+'/api/v1/system/ready',timeout=5) as r:
  ready=json.load(r);check('api_database_and_signer_ready',ready['checks']=={'database':'ok','signing_key':'ok'})
 plain=LicenseApi(BASE)
 check('unauthenticated_room_creation_401',refuses(lambda:plain.call('POST','/network/lan/rooms',{}),401))
 credentials=json.loads(Path('/srv/akeno-network-staging/source/deploy/network-staging/runtime/test-users.json').read_bytes())
 check('bad_password_login_401',refuses(lambda:plain.call('POST','/auth/login',{'email':'pc-a@akeno-staging.invalid','password':'deliberately-invalid-smoke-password'}),401))
 keydoc=json.loads((OUT/'staging-license-public-keys.json').read_bytes());keys={k['kid']:k['public_key'] for k in keydoc['keys']}
 clients=[]
 for suffix,field in [('a','pc_a_password'),('b','pc_b_password')]:
  os.environ['AKENO_DEVICE_ID']='akeno-local-smoke-'+suffix
  manager=LicenseManager(LicensingConfig(BASE,keys,website_url='https://lan-staging.akeno.online'),MemoryStore())
  CURRENT='current_client_sign_in_'+suffix
  view=manager.sign_in('pc-'+suffix+'@akeno-staging.invalid',credentials[field],remember=False)
  check('current_client_login_and_lease_'+suffix,view.status in UNLOCKED)
  check('ordinary_verified_account_'+suffix,manager.state['account']['role']=='user' and manager.state['account']['email_verified'])
  lic=api(manager,'GET','/licenses/current');check('active_limited_test_license_'+suffix,lic['active'] and lic['device_limit']==1 and not lic['lifetime'])
  check('client_only_trusts_staging_key_'+suffix,set(manager.trusted_keys())==set(keys))
  clients.append((manager,LanClient(manager,MemoryStore())))
 a,la=clients[0];b,lb=clients[1]
 check('different_bound_devices',a.state['device_id']!=b.state['device_id'])
 check('other_users_device_rejected_403',refuses(lambda:api(b,'POST','/network/lan/rooms',{'device_id':a.state['device_id'],'public_key':Member().key,'name':'forbidden'}),403))
 check('body_actor_spoof_rejected_422',refuses(lambda:api(a,'POST','/network/lan/rooms',{'device_id':a.state['device_id'],'public_key':Member().key,'name':'forbidden','actor':'fake'}),422))
 CURRENT='actual_lan_client_create'
 room=la.create('Local staging smoke room');rid=room['room_id'];check('lan_client_create_room',len(rid)==32)
 check('lan_client_join_same_room',lb.join(room['invite'])['room_id']==rid)
 ma,mb=Member(),Member()
 # Use the actual LanClient-generated private identities in RAM only; never serialize them to disk/output.
 from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
 for member,lan in [(ma,la),(mb,lb)]:
  member.private=X25519PrivateKey.from_private_bytes(base64.b64decode(lan.identity.load()['private_key']))
  member.public=wg.raw_public(member.private);member.key=wg.b64(member.public)
 CURRENT='initial_authenticated_heartbeats'
 ha,hb=la.heartbeat(rid),lb.heartbeat(rid)
 check('two_members_visible',len(ha['members'])==len(hb['members'])==2)
 c=ha['coordinator'];check('loopback_wireguard_coordinator_advertised',ha['transport']=='wireguard' and c['host']=='127.0.0.1' and c['port']==3478)
 pub=base64.b64decode(c['public_key'],validate=True)
 check('coordinator_public_key_valid',len(pub)==32)
 (OUT/'staging-wireguard-public-key.json').write_text(json.dumps({'algorithm':'X25519/WireGuard','public_key':c['public_key'],'local_host':'127.0.0.1','port':3478,'private_key_exported':False},indent=2)+'\n')
 try:
  h,p,sig=ha['credential'].split('.')
  raw=bytearray(base64.urlsafe_b64decode(sig+'='*(-len(sig)%4)));raw[0]^=1
  changed=h+'.'+p+'.'+base64.urlsafe_b64encode(raw).rstrip(b'=').decode()
  verify_peer(changed,a.trusted_keys(),rid)
  tamper_rejected=False
 except Exception:tamper_rejected=True
 check('forged_peer_credential_rejected',tamper_rejected)
 sa,sb,sx=udp(),udp(),udp()
 try:
  stranger=Member();check('unregistered_wireguard_key_no_response',silent(sx,stranger.initiation(pub)))
  proof={}
  for member,sock,label in [(ma,sa,'a'),(mb,sb,'b')]:
   packet=member.initiation(pub);proof[label]=packet;sock.sendto(packet,DEST)
   member.consume(sock.recvfrom(2048)[0]);check('real_wireguard_endpoint_proof_'+label,member.remote is not None)
   sock.sendto(member.keepalive(),DEST)
  ha,hb=la.heartbeat(rid),lb.heartbeat(rid)
  own_a=verify_peer(ha['credential'],a.trusted_keys(),rid);own_b=verify_peer(hb['credential'],b.trusted_keys(),rid)
  check('signed_endpoints_after_proof',own_a['endpoint'] is not None and own_b['endpoint'] is not None and own_a['endpoint']!=own_b['endpoint'])
  peer=verify_peer(ha['peers'][0]['credential'],a.trusted_keys(),rid)
  check('signed_peer_device_key_and_lease',peer['public_key']==mb.key and peer['device_id']==b.state['device_id'] and 0<peer['exp']-peer['iat']<=45)
  check('handshake_replay_no_response',silent(sx,proof['a']))
  same=verify_peer(la.heartbeat(rid)['credential'],a.trusted_keys(),rid)
  check('replay_cannot_move_endpoint',same['endpoint']==own_a['endpoint'])
  # Real end-to-end member-to-member Noise handshake through the deployed opaque relay.
  responder=wg.Responder(mb.private,lambda public:public==ma.public,time.monotonic)
  initiation=ma.initiation(mb.public);sa.sendto(initiation,DEST)
  received=sb.recvfrom(2048)[0];check('relay_initiation_bytes_unchanged',received==initiation)
  reply,public=responder.initiation(received);sb.sendto(reply,DEST);response=sa.recvfrom(2048)[0]
  check('relay_response_bytes_unchanged',response==reply and public==ma.public);ma.consume(response)
  session=responder.sessions[ma.remote]
  count=64
  for i in range(count):
   payload=i.to_bytes(4,'little')+bytes([i])*1196
   msg=bytes([4,0,0,0])+ma.remote+struct.pack('<Q',i)+ma.send_key.encrypt(wg.nonce(i),payload,None)
   sa.sendto(msg,DEST);at_b=sb.recvfrom(2048)[0]
   assert at_b==msg and session.recv.decrypt(wg.nonce(i),at_b[16:],None)==payload
   echo=bytes([4,0,0,0])+session.remote+struct.pack('<Q',i)+session.send.encrypt(wg.nonce(i),payload,None)
   sb.sendto(echo,DEST);at_a=sa.recvfrom(2048)[0]
   assert at_a==echo and ma.recv_key.decrypt(wg.nonce(i),at_a[16:],None)==payload
  check('64_real_encrypted_1200_byte_roundtrips',True)
  check('invalid_udp_datagram_no_response',silent(sx,b'not-a-wireguard-packet'))
  check('non_owner_cannot_rotate_invite',refuses(lambda:lb.rotate_invite(rid),403))
  fresh=la.rotate_invite(rid);check('owner_rotates_invite',fresh['invite']!=room['invite'])
  check('old_invite_rejected',refuses(lambda:lb.join(room['invite']),403))
  la.remove_member(rid,b.state['account']['id'])
  check('removed_member_heartbeat_rejected',refuses(lambda:lb.heartbeat(rid),403))
  # Immediate relay path block, not merely an HTTP response.
  blocked=bytes([4,0,0,0])+ma.remote+struct.pack('<Q',100)+ma.send_key.encrypt(wg.nonce(100),b'x'*16,None)
  sa.sendto(blocked,DEST)
  try:sb.recvfrom(2048);blocked_ok=False
  except socket.timeout:blocked_ok=True
  check('removed_member_relay_stops',blocked_ok)
  check('removed_member_cannot_reprove',silent(sb,mb.initiation(pub)))
  check('banned_member_cannot_rejoin',refuses(lambda:lb.join(fresh['invite']),403))
  la.delete(rid);check('deleted_room_unavailable',refuses(lambda:la.heartbeat(rid),403))
  check('deleted_room_coordinator_denies_proof',silent(sa,ma.initiation(pub)))
 finally:
  for sock in [sa,sb,sx]:sock.close()
 for manager,lan in clients:
  check('prior_smoke_sessions_revoked_'+manager.state['account']['display_name'],api(manager,'POST','/auth/sessions/revoke-others')['revoked']>=0)
  check('smoke_device_removed_'+manager.state['account']['display_name'],api(manager,'DELETE','/devices/'+manager.state['device_id'])=={})
  manager.sign_out()
 check('client_signouts_complete',all(not m.state.get('account') for m,_ in clients))
 return {'encrypted_roundtrips':64,'payload_bytes_each':1200,'actual_claude_client_source':'588b21ca64a78f13d937a89abb58d5289b34f7c5','memory_only_client_states':True,'private_wireguard_member_keys_saved':False,'production_requests_performed':False,'staging_test_rooms_deleted':True,'synthetic_smoke_devices_revoked':True,'real_windows_or_isp_nat_test':False}

if __name__=='__main__':
 try:
  extra=main();result={'passed':True,'checks':RESULTS,'count':len(RESULTS),'at_berlin':datetime.now(ZoneInfo('Europe/Berlin')).isoformat(),**extra}
 except Exception as e:
  result={'passed':False,'checks':RESULTS,'count':len(RESULTS),'failed_stage':CURRENT,'error_type':type(e).__name__,'no_secret_exception_details_printed':True}
 (OUT/'LIVE_SMOKE_RESULTS.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
 print(json.dumps({k:v for k,v in result.items() if k!='checks'},ensure_ascii=False))
 raise SystemExit(0 if result['passed'] else 1)
