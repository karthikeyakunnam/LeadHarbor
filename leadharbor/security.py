import base64
import hashlib
import hmac
import ipaddress
import json
import re
import secrets
import time
from urllib.parse import urlsplit, urlunsplit

FREE_MAIL = {'gmail.com', 'googlemail.com', 'yahoo.com', 'outlook.com', 'hotmail.com', 'icloud.com',
             'proton.me', 'protonmail.com', 'aol.com', 'live.com', 'mail.com', 'yandex.com', 'gmx.com'}


class ValidationError(ValueError):
    pass


def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 600000).hex()
    return f'pbkdf2_sha256$600000${salt}${digest}'


def password_matches(password, stored):
    try:
        kind, rounds, salt, digest = stored.split('$')
        computed = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), int(rounds)).hex()
        return kind == 'pbkdf2_sha256' and hmac.compare_digest(computed, digest)
    except (ValueError, TypeError):
        return False


def session_token(secret):
    payload = base64.urlsafe_b64encode(json.dumps({'exp': int(time.time()) + 28800, 'nonce': secrets.token_hex(16)}).encode()).decode()
    signature = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return payload + '.' + signature


def session_valid(token, secret):
    try:
        payload, sig = token.split('.')
        expected = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(sig, expected) and json.loads(base64.urlsafe_b64decode(payload))['exp'] > time.time()
    except (ValueError, KeyError, TypeError):
        return False


def public_ip(raw):
    ip = ipaddress.ip_address(raw)
    if not ip.is_global or ip.is_multicast or ip.is_reserved or ip.is_unspecified or ip.is_loopback or ip.is_link_local:
        return False
    # Reject transition/translation ranges even on older supported Python versions.
    if ip.version == 6:
        denied = ('::/96', '::ffff:0:0/96', '64:ff9b::/96', '64:ff9b:1::/48', '100::/64',
                  '2001::/23', '2001:db8::/32', '2002::/16', '3fff::/20', '5f00::/16', 'fec0::/10')
        return not any(ip in ipaddress.ip_network(cidr) for cidr in denied)
    return ip not in ipaddress.ip_network('192.0.0.0/24')


def website_url(raw):
    raw = str(raw or '').strip()
    if not raw:
        return ''
    if len(raw) > 2048 or re.search(r'[\s\\\x00-\x1f\x7f]', raw):
        raise ValidationError('Enter a valid company website URL.')
    if '://' not in raw:
        raw = 'https://' + raw
    try:
        u = urlsplit(raw)
        if u.scheme not in ('http', 'https') or not u.hostname or u.username is not None or u.password is not None:
            raise ValueError()
        if u.port not in (None, 80, 443):
            raise ValueError()
        host = u.hostname.rstrip('.').encode('idna').decode().lower()
        try:
            ipaddress.ip_address(host)
        except ValueError:
            if not re.fullmatch(r'(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{1,62}', host):
                raise ValueError()
            if host.endswith(('.localhost', '.local', '.internal', '.test', '.invalid', '.example', '.home', '.lan')):
                raise ValueError()
        else:
            if not public_ip(host):
                raise ValueError()
        hostname = f'[{host}]' if ':' in host else host
        port = f':{u.port}' if u.port else ''
        return urlunsplit((u.scheme, hostname + port, u.path or '/', u.query, ''))
    except (ValueError, UnicodeError):
        raise ValidationError('Website must use a public HTTP(S) host on port 80 or 443.') from None


def normalize_lead(form):
    if not isinstance(form, dict):
        raise ValidationError('Submission must be an object.')
    def value(key, alias, maximum, required=True):
        v = form.get(key, form.get(alias, ''))
        if not isinstance(v, str):
            raise ValidationError(f'{key} must be text.')
        v = v.strip()
        if (required and not v) or len(v) > maximum or any(ord(c) < 32 and c not in '\n\r\t' for c in v):
            raise ValidationError(f'{key} is required and must contain at most {maximum} characters.')
        return v
    name = value('full_name', 'Full name', 150)
    email = value('email', 'Work email', 254).lower()
    if not re.fullmatch(r"[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}", email):
        raise ValidationError('Enter a valid email address.')
    message = value('message', 'Message', 6000)
    site = value('website_url', 'Company website', 2048, False)
    domain = email.rsplit('@', 1)[1]
    if not site and domain not in FREE_MAIL:
        # A non-public email suffix must not prevent receipt; simply skip fallback enrichment.
        try:
            site = website_url(domain)
        except ValidationError:
            site = ''
    site = website_url(site)
    return {'full_name': name, 'email': email, 'message': message, 'website_url': site,
            'company_domain': urlsplit(site).hostname or '', 'is_free_mail': domain in FREE_MAIL}
