#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# what:   Install the deploy agent as a service on the range host that has Docker
#         (k8s-blue, 192.168.40.59), polling the website on the platform VM.
# usage:  sudo bash install-on-range.sh [--base https://192.168.40.11] [--with-trivy]
#         run from a folder holding: agent.py, website.kubeconfig, jengasec-ca.crt
# asks:   the deploy token (same value as DJANGO_DEPLOY_TOKEN on the website), typed hidden
# changes:/opt/jengasec-deploy-agent (code), /etc/jengasec-deploy-agent (token, kubeconfig, 0600),
#         the website's CA in the system trust store, jengasec-deploy-agent.service
# safe to run again: yes (keeps the stored token unless you answer with a new one)
# ---------------------------------------------------------------------------
set -euo pipefail
BASE=https://192.168.40.11; TRIVY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --base) BASE="$2"; shift ;;
    --with-trivy) TRIVY=1 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done
[ "$(id -u)" = 0 ] || { echo "run with sudo" >&2; exit 1; }
HERE="$(cd "$(dirname "$0")" && pwd)"
for f in agent.py website.kubeconfig jengasec-ca.crt; do
  [ -s "$HERE/$f" ] || { echo "missing $HERE/$f" >&2; exit 1; }
done
command -v docker >/dev/null || { echo "Docker is not installed here" >&2; exit 1; }
command -v kubectl >/dev/null || { echo "kubectl is not installed here" >&2; exit 1; }
umask 022

APP=/opt/jengasec-deploy-agent; ETC=/etc/jengasec-deploy-agent
install -d -m 755 "$APP"; install -d -m 700 "$ETC"
install -m 644 "$HERE/agent.py" "$APP/agent.py"
install -m 600 "$HERE/website.kubeconfig" "$ETC/kubeconfig"
install -m 644 "$HERE/jengasec-ca.crt" /usr/local/share/ca-certificates/jengasec-ca.crt
update-ca-certificates >/dev/null

if [ "$TRIVY" = 1 ] && ! command -v trivy >/dev/null; then
  echo "installing Trivy (image scanner) from GitHub"
  curl -sfL https://raw.githubusercontent.com/aquasecurity/trivy/main/contrib/install.sh | sh -s -- -b /usr/local/bin
fi

old=""; [ -f "$ETC/agent.env" ] && old=$(sed -n 's/^JENGASEC_DEPLOY_TOKEN=//p' "$ETC/agent.env")
read -r -s -p "Deploy token (DJANGO_DEPLOY_TOKEN on the website)${old:+, Enter to keep the stored one}: " token; echo
token="${token:-$old}"
[ -n "$token" ] || { echo "no token given" >&2; exit 1; }
umask 077
cat > "$ETC/agent.env" <<EOF
JENGASEC_BASE=$BASE
JENGASEC_DEPLOY_TOKEN=$token
KUBECONFIG=$ETC/kubeconfig
EOF
umask 022

# The token must work before the service starts: 200 = accepted, 403 = wrong token, 404 = not set on the site.
hdr=$(mktemp); chmod 600 "$hdr"; printf 'X-Deploy-Token: %s\n' "$token" > "$hdr"   # never on argv (ps)
body=$(mktemp)
code=$(curl -s -o "$body" -w '%{http_code}' -H @"$hdr" "$BASE/deployments/api/pending/" || true)
rm -f "$hdr"
case "$code" in
  200) echo "website accepts the token" ;;
  # 409: the token is fine, but a submitted team is not ready to deploy (no cell or reserved IP).
  # The agent retries every 30 s, so install anyway and say which team to fix on the website.
  409) echo "website accepts the token, but a submission cannot be deployed yet:"
       echo "  $(head -c 300 "$body")"
       echo "  fix that team on the website (enterprise, cell and reserved service IP); the agent retries by itself" ;;
  403) echo "website refused the token: it differs from DJANGO_DEPLOY_TOKEN" >&2; rm -f "$body"; exit 1 ;;
  404) echo "website answered 404: DJANGO_DEPLOY_TOKEN is not set there" >&2; rm -f "$body"; exit 1 ;;
  *)   echo "website answered $code: $(head -c 300 "$body")" >&2; rm -f "$body"; exit 1 ;;
esac
rm -f "$body"
KUBECONFIG="$ETC/kubeconfig" kubectl auth can-i patch deployments/app -n jengasec-ent-a-app01 >/dev/null \
  || { echo "the kubeconfig may not deploy into the cells" >&2; exit 1; }

cat > /etc/systemd/system/jengasec-deploy-agent.service <<EOF
[Unit]
Description=JengaSec deploy agent (website submissions -> k8s-blue cells)
After=network-online.target docker.service
Wants=network-online.target

[Service]
EnvironmentFile=$ETC/agent.env
ExecStart=/usr/bin/python3 $APP/agent.py --watch --interval 30
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now jengasec-deploy-agent >/dev/null
sleep 3
systemctl --no-pager --lines=8 status jengasec-deploy-agent || true
echo "done. Follow it with: sudo journalctl -u jengasec-deploy-agent -f"
