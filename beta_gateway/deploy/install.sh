#!/usr/bin/env bash
# Run on a new Ubuntu server after DNS, filing and firewall setup.
set -euo pipefail
umask 077

if [[ ${1:-} == --help || $# != 2 ]]; then
    echo 'Usage: sudo bash deploy/install.sh api.your-domain.com certificate-email@example.com'
    echo 'Requires Ubuntu 22.04/24.04, domain DNS + ICP filing, inbound 80/443 and outbound HTTPS.'
    echo 'The DeepSeek key is entered privately on the server, never as a command argument.'
    if [[ ${1:-} == --help ]]; then exit 0; else exit 2; fi
fi
DOMAIN=$1
EMAIL=$2
SOURCE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
[[ $EUID == 0 ]] || { echo 'Run with sudo.' >&2; exit 1; }
command -v python3 >/dev/null || { echo 'Install python3 first.' >&2; exit 1; }
python3 - "$DOMAIN" "$EMAIL" <<'PY'
import ipaddress, re, sys
domain, email = sys.argv[1:]
label = r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?'
if len(domain) > 253 or not re.fullmatch(label + r'(?:\.' + label + r')+', domain):
    sys.exit('Use a lowercase ASCII domain (for example api.example.com).')
try:
    ipaddress.ip_address(domain)
except ValueError:
    pass
else:
    sys.exit('Use a domain, not an IP address.')
if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email):
    sys.exit('Provide a valid certificate contact email.')
PY
source /etc/os-release
[[ $ID == ubuntu && ( $VERSION_ID == 22.04 || $VERSION_ID == 24.04 ) ]] || {
    echo 'This installer supports Ubuntu 22.04 and 24.04.' >&2; exit 1;
}
CONFIG=/etc/nginx/sites-available/chatseek-gateway
if [[ -e $CONFIG ]] && ! head -n 1 "$CONFIG" | grep -qx '# Managed by ChatSeek.'; then
    echo "Refusing to overwrite unmanaged file: $CONFIG" >&2; exit 1
fi
if [[ -f /etc/chatseek-gateway-domain ]] && [[ $(cat /etc/chatseek-gateway-domain) != "$DOMAIN" ]]; then
    echo 'A different ChatSeek domain is already configured. Migrate it manually.' >&2; exit 1
fi
if [[ ! -s /etc/chatseek-gateway.env ]]; then
    read -r -s -p 'DeepSeek API Key (hidden, entered only on this server): ' CHATSEEK_DEPLOY_KEY </dev/tty
    echo
    [[ $CHATSEEK_DEPLOY_KEY =~ ^[A-Za-z0-9_-]+$ ]] || { echo 'Invalid or empty key.' >&2; exit 1; }
fi

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y python3 nginx certbot ca-certificates
if ! id chatseek >/dev/null 2>&1; then
    useradd --system --home-dir /nonexistent --no-create-home --shell /usr/sbin/nologin chatseek
fi
[[ $(id -u chatseek) != 0 ]] || { echo 'chatseek must not be root.' >&2; exit 1; }
install -d -m 0755 /opt/chatseek /var/www/chatseek-acme /etc/nginx/snippets
install -d -o chatseek -g chatseek -m 0700 /var/lib/chatseek
install -m 0644 "$SOURCE/server.py" /opt/chatseek/server.py
install -m 0644 "$SOURCE/deploy/backup.py" /opt/chatseek/backup.py
install -m 0644 "$SOURCE/deploy/check_gateway.py" /opt/chatseek/check_gateway.py
install -m 0644 "$SOURCE/chatseek-gateway.service" /etc/systemd/system/chatseek-gateway.service
install -m 0644 "$SOURCE/deploy/chatseek-backup.service" /etc/systemd/system/chatseek-backup.service
install -m 0644 "$SOURCE/deploy/chatseek-backup.timer" /etc/systemd/system/chatseek-backup.timer
install -m 0644 "$SOURCE/deploy/proxy.conf" /etc/nginx/snippets/chatseek-proxy.conf
if [[ -n ${CHATSEEK_DEPLOY_KEY:-} ]]; then
    printf 'DEEPSEEK_API_KEY=%s\n' "$CHATSEEK_DEPLOY_KEY" > /etc/chatseek-gateway.env
    unset CHATSEEK_DEPLOY_KEY
fi
chown root:root /etc/chatseek-gateway.env
chmod 0600 /etc/chatseek-gateway.env
systemctl daemon-reload
systemctl enable --now chatseek-gateway.service
systemctl restart chatseek-gateway.service
for CHATSEEK_ATTEMPT in {1..10}; do
    if python3 -c 'import urllib.request; urllib.request.urlopen("http://127.0.0.1:8787/health", timeout=2).close()' 2>/dev/null; then break; fi
    sleep 1
done
python3 -c 'import urllib.request; urllib.request.urlopen("http://127.0.0.1:8787/health", timeout=2).close()'

if [[ ! -f /etc/letsencrypt/live/$DOMAIN/fullchain.pem ]]; then
    # This temporary HTTP configuration exposes only the ACME challenge.
    sed "s/__DOMAIN__/$DOMAIN/g" "$SOURCE/deploy/nginx-http.conf" > "$CONFIG"
    chmod 0644 "$CONFIG"
    ln -sfn "$CONFIG" /etc/nginx/sites-enabled/chatseek-gateway
    nginx -t
    systemctl enable --now nginx
    systemctl reload nginx
fi
certbot certonly --webroot -w /var/www/chatseek-acme -d "$DOMAIN" \
    --email "$EMAIL" --agree-tos --non-interactive --keep-until-expiring
sed "s/__DOMAIN__/$DOMAIN/g" "$SOURCE/deploy/nginx-https.conf" > "$CONFIG"
chmod 0644 "$CONFIG"
ln -sfn "$CONFIG" /etc/nginx/sites-enabled/chatseek-gateway
nginx -t
systemctl enable --now nginx
systemctl reload nginx
install -d -m 0755 /etc/letsencrypt/renewal-hooks/deploy
printf '%s\n' '#!/bin/sh' '/usr/sbin/nginx -t && /bin/systemctl reload nginx' \
    > /etc/letsencrypt/renewal-hooks/deploy/chatseek-nginx
chmod 0755 /etc/letsencrypt/renewal-hooks/deploy/chatseek-nginx
printf '%s\n' "$DOMAIN" > /etc/chatseek-gateway-domain
systemctl enable --now certbot.timer chatseek-backup.timer
systemctl start chatseek-backup.service
python3 /opt/chatseek/check_gateway.py "https://$DOMAIN"
echo 'Gateway ready. No invites issued and no paid model calls made.'
echo 'Next: verify externally, then issue an invite with the command in DEPLOY.md.'
