#!/usr/bin/env bash
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive

apt-get update
apt-get install -y docker.io docker-compose-v2
systemctl enable --now docker

install -d -m 0750 -o 1000 -g 0 /srv/elasticsearch/data

printf '%s\n' 'vm.max_map_count=1048576' \
  >/etc/sysctl.d/99-elasticsearch.conf
sysctl --system

swapoff -a
sed -i.bak '/\sswap\s/s/^/#/' /etc/fstab
