"""Synthetic VPN Gate catalog rows with real (throwaway) certificates for the free VPN tests."""
import base64
import csv
import io

from akeno.network.vpngate import HEADER

import datetime

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PEM_KEY = KEY.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                            serialization.NoEncryption()).decode()


def certificate(name):
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    now = datetime.datetime.now(datetime.timezone.utc)
    return (x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(KEY.public_key())
            .serial_number(1).not_valid_before(now).not_valid_after(now + datetime.timedelta(days=30))
            .sign(KEY, hashes.SHA256()).public_bytes(serialization.Encoding.PEM).decode())


CA, CERT = certificate('ca'), certificate('client')


def vpn_profile(ip, proto='tcp', port=443, extra=''):
    return (f'dev tun\nproto {proto}\nremote {ip} {port}\ncipher AES-128-CBC\nauth SHA1\nclient\n{extra}'
            f'<ca>\n{CA}</ca>\n<cert>\n{CERT}</cert>\n<key>\n{PEM_KEY}</key>\n')


def row(ip='45.76.10.7', country='US', name='United States', ping='20', speed='50000000', sessions='5',
        uptime=str(48 * 3600 * 1000), host='public-vpn-1', proto='tcp', profile=None, score='1000'):
    blob = base64.b64encode((profile if profile is not None else vpn_profile(ip, proto)).encode()).decode()
    return [host, ip, score, ping, speed, name, country, sessions, uptime, '1', '1', '2weeks', 'Academic Use Only', '', blob]


def catalog(*rows):
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(['*vpn_servers'])
    w.writerow(['#' + HEADER[0], *HEADER[1:]])
    for r in rows or [row()]:
        w.writerow(r)
    w.writerow(['*'])
    return out.getvalue().encode()




def row_for(ip, country, **kw):
    return row(ip=ip, country=country, name={'US': 'United States', 'JP': 'Japan'}.get(country, country), **kw)
