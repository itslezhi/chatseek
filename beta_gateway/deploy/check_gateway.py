#!/usr/bin/env python3
"""Check public HTTPS and authentication, without invites or paid model calls."""
import argparse
import json
import urllib.error
import urllib.parse
import urllib.request


def check(base):
    parts = urllib.parse.urlsplit(base)
    if (parts.scheme != 'https' or not parts.hostname or parts.username or parts.password
            or parts.path not in ('', '/') or parts.query or parts.fragment):
        raise ValueError('provide an HTTPS origin without credentials or a path')
    base = base.rstrip('/')
    for path, expected, expected_body in (
            ('/health', 200, {'ok': True}),
            ('/v1/quota', 401, {'error': 'missing_token'})):
        request = urllib.request.Request(base + path, headers={'Accept': 'application/json'})
        try:
            response = urllib.request.urlopen(request, timeout=20)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            if response.geturl() != base + path:
                raise RuntimeError('unexpected redirect: ' + path)
            body = json.loads(response.read(4096))
            if response.status != expected or body != expected_body:
                raise RuntimeError('unexpected response: ' + path)
        print('OK', path, expected)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('url')
    check(parser.parse_args().url)
