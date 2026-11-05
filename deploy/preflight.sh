#!/bin/sh
set -eu
printf '%s\n' 'Read-only workbench host preflight. No secrets or service changes.'
hostname
uname -s
python3 --version
free -m
df -h /
ss -ltn
systemctl is-active nginx || true
# Check other application services separately using verified service names.
systemctl is-active bolai-workbench || true
printf '%s\n' 'Do not start until the target loopback port is free or belongs to this app.'
