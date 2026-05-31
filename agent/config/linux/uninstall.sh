#!/bin/bash
if [ "$EUID" -ne 0 ]; then
    echo "Please run as root"
    exit 1
fi

echo "=== Uninstalling Radegast EDR Agent and Rustinel ==="
echo "WARNING: This will permanently remove Radegast EDR Agent and Rustinel from this system, including all configurations and logs. This action cannot be undone."
echo "WARNING: If you plan to move to another device, please backup your signing key on /opt/radegast/state/device_key"
ask confirmation() {
    read -p "Are you sure you want to uninstall Radegast EDR Agent and Rustinel? (y/N) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "Uninstallation cancelled."
        exit 0
    fi
}

echo "Stopping radegast-agent service..."
systemctl stop radegast-agent
echo "Disabling radegast-agent service..."
systemctl disable radegast-agent
echo "Stopping rustinel service..."
systemctl stop rustinel
echo "Disabling rustinel service..."
systemctl disable rustinel
echo "Deleting radegast-agent user and home directory..."
userdel -r radegast-agent
echo "Removing radegast-agent and rustinel systemd service files..."
rm -f /etc/systemd/system/radegast-agent.service
rm -f /etc/systemd/system/rustinel.service
echo "Reloading systemd daemon..."
systemctl daemon-reload
echo "Deleting radegast and rustinel files..."
rm -rf /opt/radegast
echo "Purging journal logs for radegast-agent and rustinel..."
journalctl --rotate
journalctl --vacuum-time=1s --unit=radegast-agent
journalctl --vacuum-time=1s --unit=rustinel
echo "Purging log files for radegast-agent and rustinel..."
rm -f /var/log/rustinel
echo "Uninstallation complete."