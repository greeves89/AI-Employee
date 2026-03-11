#!/bin/bash
# Install Docker Engine on Ubuntu 22.04 for the demo VM
set -euo pipefail

echo "==> Installing Docker Engine..."

# Add Docker's official GPG key
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | \
    sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg

# Add Docker repository
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
  https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

sudo apt-get update -qq
sudo apt-get install -y \
    docker-ce \
    docker-ce-cli \
    containerd.io \
    docker-buildx-plugin \
    docker-compose-plugin

# Add demo user to docker group
sudo usermod -aG docker "${DEMO_USER:-demo}"

# Enable and start Docker
sudo systemctl enable docker
sudo systemctl start docker

echo "==> Docker installed: $(docker --version)"
echo "==> Docker Compose installed: $(docker compose version)"
