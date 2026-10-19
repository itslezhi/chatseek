#!/usr/bin/env bash
# Prepare a new server while domain registration / ICP filing is pending.
set -euo pipefail
umask 077
[[ $EUID == 0 ]] || { echo 'Run with sudo.' >&2; exit 1; }
SOURCE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
source /etc/os-release
[[ $ID == ubuntu && ( $VERSION_ID == 22.04 || $VERSION_ID == 24.04 ) ]] || {
    echo 'Ubuntu 22.04 or 24.04 required.' >&2; exit 1;
}
if systemctl is-active --quiet chatseek-gateway.service || [[ -e /etc/chatseek-gateway.env ]]; then
    echo 'Gateway already configured; inspect it before staging changes.' >&2
    exit 1
fi
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y python3 ca-certificates
if ! id chatseek >/dev/null 2>&1; then
    useradd --system --home-dir /nonexistent --no-create-home --shell /usr/sbin/nologin chatseek
fi
[[ $(id -u chatseek) != 0 ]] || { echo 'chatseek must not be root.' >&2; exit 1; }
install -d -m 0755 /opt/chatseek
install -d -o chatseek -g chatseek -m 0700 /var/lib/chatseek
install -m 0644 "$SOURCE/server.py" /opt/chatseek/server.py
install -m 0644 "$SOURCE/deploy/backup.py" /opt/chatseek/backup.py
install -m 0644 "$SOURCE/deploy/check_gateway.py" /opt/chatseek/check_gateway.py
install -m 0644 "$SOURCE/chatseek-gateway.service" /etc/systemd/system/chatseek-gateway.service
install -m 0644 "$SOURCE/deploy/chatseek-backup.service" /etc/systemd/system/chatseek-backup.service
install -m 0644 "$SOURCE/deploy/chatseek-backup.timer" /etc/systemd/system/chatseek-backup.timer
python3 -m py_compile /opt/chatseek/server.py /opt/chatseek/backup.py /opt/chatseek/check_gateway.py
systemctl daemon-reload
sudo -u chatseek python3 - <<'PY'
import sys, tempfile
from pathlib import Path
sys.path.insert(0, '/opt/chatseek')
from server import Ledger, Denied
from backup import backup
with tempfile.TemporaryDirectory(prefix='chatseek-smoke-') as temporary:
    root = Path(temporary)
    ledger = Ledger(root / 'usage.sqlite3')
    token = ledger.activate(ledger.issue('synthetic-smoke'), 'synthetic-installation-0001')['token']
    assert ledger.quota(token)['calls_remaining'] == 200
    request = ledger.reserve(token, 100)
    ledger.settle(request, 10)
    assert ledger.quota(token)['calls_remaining'] == 199
    copy = backup(ledger.path, root / 'backups')
    assert copy.exists()
    try:
        ledger.quota('invalid-token')
    except Denied:
        pass
    else:
        raise RuntimeError('unauthorized quota access allowed')
print('PASS: synthetic activation, quota accounting, authentication, SQLite backup')
PY
echo 'STAGED: /opt/chatseek, private data directory, systemd units.'
echo 'No API key installed; gateway and backup timer have not been started or enabled.'
echo 'No HTTP/HTTPS listener, firewall rule, invite or paid model request created.'
echo 'After domain registration, ICP filing, DNS and firewall setup, run deploy/install.sh.'
