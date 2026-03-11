#!/bin/bash
# Clean up the VM to minimize OVA size before export
set -euo pipefail

echo "==> Cleaning up to reduce image size..."

# Remove build dependencies and caches
sudo apt-get autoremove -y
sudo apt-get clean
sudo rm -rf /var/lib/apt/lists/*

# Clear tmp
sudo rm -rf /tmp/* /var/tmp/*

# Clear bash history
history -c
sudo sh -c 'cat /dev/null > /root/.bash_history'
cat /dev/null > "/home/${DEMO_USER:-demo}/.bash_history"

# Remove cloud-init instance data (so it re-runs on first boot)
sudo cloud-init clean --logs

# Zero out free space for better compression
# (takes a while but significantly reduces OVA size)
sudo dd if=/dev/zero of=/EMPTY bs=1M 2>/dev/null || true
sudo rm -f /EMPTY
sync

echo "==> Cleanup complete."
