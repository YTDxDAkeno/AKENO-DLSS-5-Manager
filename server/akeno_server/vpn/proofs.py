"""Independent exit-probe attestations. No configured public probe key means no verified VPN country.

The trusted probe (separate from operator and exit agent) must observe the exit IPv4/IPv6 through the tunnel,
check a licensed/reviewed GeoIP source and sign its measurement. Production trust keys are provisioned offline.
"""
import json
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from akeno_server.auth.tokens import b64d
from akeno_server.licensing.signing import verify, kid_for
from akeno_server.vpn.store import VPNError, global_ip, country_code


def verify_proof(token, path, server_id, server_key, now):
    try:
        if not isinstance(token, str) or len(token) > 8192:
            raise ValueError()
        raw = Path(path).read_bytes()
        if len(raw) > 16384:
            raise ValueError()
        document = json.loads(raw)
        if not isinstance(document, dict) or set(document) != {'keys'} or not 1 <= len(document['keys']) <= 8:
            raise ValueError()
        keys = {}
        for kid, value in document['keys'].items():
            key = b64d(value)
            if kid_for(key) != kid:
                raise ValueError()
            keys[kid] = Ed25519PublicKey.from_public_bytes(key)
        _, claims = verify(token, keys, 'akeno-vpn-exit-proof')
        required = {'iss', 'aud', 'server_id', 'server_public_key', 'country', 'exit_ipv4', 'exit_ipv6',
                    'observed_at', 'iat', 'exp', 'nonce', 'evidence'}
        if set(claims) != required or claims['iss'] != 'akeno-vpn-probe' or claims['aud'] != server_id or \
                claims['server_id'] != server_id or claims['server_public_key'] != server_key:
            raise ValueError()
        if any(type(claims[k]) is not int for k in ('iat', 'exp', 'observed_at')) or \
                not claims['iat'] <= now < claims['exp'] or not 0 < claims['exp'] - claims['iat'] <= 300 or \
                not now - 300 <= claims['observed_at'] <= now + 5:
            raise ValueError()
        nonce = claims['nonce']
        if not isinstance(nonce, str) or not 24 <= len(nonce) <= 128 or not nonce.isascii():
            raise ValueError()
        country_code(claims['country']); global_ip(claims['exit_ipv4'], 4); global_ip(claims['exit_ipv6'], 6)
        return claims
    except Exception:
        raise VPNError('Independent VPN exit proof is unavailable, invalid or expired.') from None
