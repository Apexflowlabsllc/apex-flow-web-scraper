#!/bin/sh
set -eu
# Invoked after uploading the exact source archive. No secrets are printed.
release=/opt/apex-webscraper/releases/20261106
mkdir -p "$release"
tar -xf /tmp/apex-webscraper-release.tar -C "$release"
python3 -m venv "$release/.venv"
"$release/.venv/bin/pip" install --disable-pip-version-check --no-cache-dir -q "$release"
cat > /etc/systemd/system/apex-webscraper.service <<'UNIT'
[Unit]
Description=Apex Flow Web Scraper hosted API
After=network-online.target
[Service]
DynamicUser=yes
StateDirectory=apex-webscraper
WorkingDirectory=/opt/apex-webscraper/releases/20261106
Environment=RAINBO_SCRAPE_DATA=/var/lib/apex-webscraper
Environment=HOME=/var/lib/apex-webscraper
Environment=RAINBO_BRAVE_PATH=/usr/bin/brave-browser
EnvironmentFile=/etc/apex-webscraper.env
ExecStart=/opt/apex-webscraper/releases/20261106/.venv/bin/python -m rainbo_scrape.hosted
Restart=on-failure
RestartSec=5
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
RestrictSUIDSGID=yes
CapabilityBoundingSet=
MemoryMax=1800M
CPUQuota=150%
TasksMax=256
UMask=0077
[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now apex-webscraper
if ! grep -q '^webscraper-api.159.223.109.53.nip.io {' /etc/caddy/Caddyfile; then
  cp -p /etc/caddy/Caddyfile /etc/caddy/Caddyfile.before-webscraper-20261106
  cat >> /etc/caddy/Caddyfile <<'CADDY'

webscraper-api.159.223.109.53.nip.io {
    request_body {
        max_size 100KB
    }
    header X-Content-Type-Options nosniff
    reverse_proxy 127.0.0.1:8850
}
CADDY
  if caddy validate --config /etc/caddy/Caddyfile >/dev/null 2>&1; then
    systemctl reload caddy
  else
    cp -p /etc/caddy/Caddyfile.before-webscraper-20261106 /etc/caddy/Caddyfile
    exit 1
  fi
fi
systemctl is-active apex-webscraper caddy
