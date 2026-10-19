#!/usr/bin/env python3
"""Build only the public gateway sources and deployment configuration."""
import argparse
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
FILES = (
    'server.py', 'README.md', 'DEPLOY.md', 'chatseek-gateway.service',
    'deploy/install.sh', 'deploy/stage.sh', 'deploy/proxy.conf', 'deploy/nginx-http.conf',
    'deploy/nginx-https.conf', 'deploy/backup.py', 'deploy/check_gateway.py',
    'deploy/chatseek-backup.service', 'deploy/chatseek-backup.timer',
)


def build(destination):
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name in FILES:
            archive.write(ROOT / 'beta_gateway' / name, 'chatseek-beta-gateway/' + name)
    return destination


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'dist/ChatSeek-beta-gateway.zip')
    print(build(parser.parse_args().output))
