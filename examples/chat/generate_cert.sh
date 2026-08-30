#!/usr/bin/env bash
# Generate a local HTTPS certificate for the RoboAgent chat example.
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 <LAN-IP>" >&2
  echo "Example: $0 192.168.10.166" >&2
  exit 2
fi

lan_ip="$1"
if [[ ! "$lan_ip" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]]; then
  echo "LAN-IP must be an IPv4 address: $lan_ip" >&2
  exit 2
fi

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
certificate_dir="$script_dir/certs"
certificate_path="$certificate_dir/roboagent-cert.pem"
private_key_path="$certificate_dir/roboagent-key.pem"

if [[ -e "$certificate_path" || -e "$private_key_path" ]]; then
  echo "Certificate files already exist in $certificate_dir; refusing to overwrite them." >&2
  exit 1
fi

mkdir -p "$certificate_dir"
umask 077
openssl req -x509 -newkey rsa:2048 -sha256 -nodes -days 365 \
  -keyout "$private_key_path" \
  -out "$certificate_path" \
  -subj "/CN=RoboAgent Local Development" \
  -addext "subjectAltName=IP:$lan_ip,IP:127.0.0.1,DNS:localhost" \
  -addext "basicConstraints=critical,CA:FALSE" \
  -addext "keyUsage=critical,digitalSignature,keyEncipherment" \
  -addext "extendedKeyUsage=serverAuth"

echo "Generated: $certificate_path"
echo "Generated: $private_key_path"
echo "Start the example again, then open: https://$lan_ip:7860"
