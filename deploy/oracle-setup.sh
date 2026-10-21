#!/usr/bin/env bash
# One-time setup for a fresh Oracle Cloud Ubuntu VM: installs Docker and opens
# ports 80/443 in the OS firewall. Oracle's Ubuntu images ship iptables rules
# that REJECT everything except SSH, on top of the VCN security list - both
# layers must allow the ports or Caddy can't get a certificate.
# Usage: bash deploy/oracle-setup.sh   (then log out and back in)
set -euo pipefail

if ! command -v docker >/dev/null; then
  curl -fsSL https://get.docker.com | sudo sh
fi
sudo usermod -aG docker "$USER"

for port in 80 443; do
  if ! sudo iptables -C INPUT -p tcp --dport "$port" -j ACCEPT 2>/dev/null; then
    reject_line=$(sudo iptables -L INPUT --line-numbers | awk '/REJECT/ {print $1; exit}')
    if [ -n "$reject_line" ]; then
      sudo iptables -I INPUT "$reject_line" -p tcp --dport "$port" -j ACCEPT
    else
      sudo iptables -A INPUT -p tcp --dport "$port" -j ACCEPT
    fi
  fi
done
sudo netfilter-persistent save

public_ip=$(curl -fsS https://api.ipify.org)
echo
echo "Done. Log out and back in so the docker group applies."
echo "Public IP: $public_ip"
echo "Suggested DOMAIN for .env: ${public_ip//./-}.sslip.io"
