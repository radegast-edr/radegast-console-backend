#!/bin/bash
# Radegast EDR Agent & Rustinel Auto-installation Script
set -e

echo "=== Starting Radegast EDR Agent & Rustinel Installation ==="

# 0. Check RADEGAST_TOKEN environment variable
if [ -z "$RADEGAST_TOKEN" ]; then
    echo "ERROR: RADEGAST_TOKEN environment variable is not set." >&2
    echo "Please run: curl ... | sudo RADEGAST_TOKEN=\"your_token\" sh" >&2
    exit 1
fi

# 1. Verify required commands
if ! command -v python3 >/dev/null 2>&1; then
    echo "ERROR: python3 is required but not installed." >&2
    exit 1
fi

if ! command -v systemctl >/dev/null 2>&1; then
    echo "ERROR: systemd is required but systemctl was not found." >&2
    exit 1
fi

# Install unzip if missing
if ! command -v unzip >/dev/null 2>&1; then
    echo "unzip is missing, attempting to install..."
    if command -v apt-get >/dev/null 2>&1; then
        apt-get update && apt-get install -y unzip
    elif command -v yum >/dev/null 2>&1; then
        yum install -y unzip
    elif command -v dnf >/dev/null 2>&1; then
        dnf install -y unzip
    else
        echo "ERROR: unzip is required but could not be installed automatically." >&2
        exit 1
    fi
fi

# Install sudo if missing
if ! command -v sudo >/dev/null 2>&1; then
    echo "sudo is missing, attempting to install..."
    if command -v apt-get >/dev/null 2>&1; then
        apt-get update && apt-get install -y sudo
    elif command -v yum >/dev/null 2>&1; then
        yum install -y sudo
    elif command -v dnf >/dev/null 2>&1; then
        dnf install -y sudo
    else
        echo "ERROR: sudo is required but could not be installed automatically." >&2
        exit 1
    fi
fi

# 2. Check platform and arch
OS_NAME=$(uname -s | tr '[:upper:]' '[:lower:]')
ARCH_NAME=$(uname -m)
if [ "$OS_NAME" != "linux" ]; then
    echo "ERROR: This install script is only for Linux." >&2
    exit 1
fi

if [ "$ARCH_NAME" = "x86_64" ]; then
    ARCH_NAME="amd64"
elif [ "$ARCH_NAME" = "aarch64" ]; then
    ARCH_NAME="arm64"
fi

# 2b. Solve Debian bug with perf_event_paranoid
# > Some Linux distributions define higher levels for kernel.perf_event_paranoid,
# > for example Debian based distributions also use kernel.perf_event_paranoid=3,
# > which disallows access to perf_event_open() without CAP_SYS_ADMIN.
# -- https://opentelemetry.io/docs/zero-code/obi/setup/kubernetes/
if [ -f /proc/sys/kernel/perf_event_paranoid ]; then
    if [ "$(cat /proc/sys/kernel/perf_event_paranoid)" -eq 3 ]; then
        echo "Solving Debian bug: kernel.perf_event_paranoid is set to 3. Setting to 2..."
        sysctl -w kernel.perf_event_paranoid=2
        if [ -d /etc/sysctl.d ]; then
            echo "kernel.perf_event_paranoid = 2" > /etc/sysctl.d/99-radegast-perf.conf
        fi
    fi
fi

# 3. Create radegast-agent system user and directories
echo "Creating radegast-agent system user..."
if ! getent group radegast-agent >/dev/null 2>&1; then
    groupadd -r radegast-agent
fi
if ! id "radegast-agent" >/dev/null 2>&1; then
    useradd -r -g radegast-agent -m -d /opt/radegast/home -s /bin/bash radegast-agent
    chmod 700 /opt/radegast/home
else
    echo "User radegast-agent already exists."
    usermod -a -G radegast-agent radegast-agent 2>/dev/null || true
fi

# Setup directories with least privileges
echo "Setting up directories and permissions..."

mkdir -p /etc/rustinel/rules/sigma
mkdir -p /etc/rustinel/rules/yara
mkdir -p /etc/rustinel/rules/ioc
touch /etc/rustinel/rules/ioc/hashes.txt
touch /etc/rustinel/rules/ioc/ips.txt
touch /etc/rustinel/rules/ioc/domains.txt
touch /etc/rustinel/rules/ioc/paths_regex.txt
chown root:radegast-agent /etc/rustinel/
chmod 750 /etc/rustinel/
chown -R root:radegast-agent /etc/rustinel/rules
chmod -R g+rwX,o-rwx /etc/rustinel/rules
find /etc/rustinel/rules -type d -exec chmod 2770 {} +
chmod 660 /etc/rustinel/rules/ioc/*.txt 2>/dev/null || true

mkdir -p /var/log/rustinel
chown root:radegast-agent /var/log/rustinel
chmod 2750 /var/log/rustinel
chgrp radegast-agent /var/log/rustinel/* 2>/dev/null || true
chmod 640 /var/log/rustinel/* 2>/dev/null || true

mkdir -p /opt/radegast/home/.config /opt/radegast/home/.local/bin /opt/radegast/home/.local/share /opt/radegast/home/.cache
chown -R radegast-agent:radegast-agent /opt/radegast/home
chmod 700 /opt/radegast/home

mkdir -p /opt/radegast/state
chown radegast-agent:radegast-agent /opt/radegast/state
chmod 700 /opt/radegast/state

# Run commands in a strictly isolated environment for radegast-agent
run_as_agent() {
    (
        cd /opt/radegast/home 2>/dev/null || cd /tmp || exit 1
        sudo -u radegast-agent -H env -i \
            HOME=/opt/radegast/home \
            USER=radegast-agent \
            LOGNAME=radegast-agent \
            PATH="/opt/radegast/home/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" \
            XDG_CONFIG_HOME=/opt/radegast/home/.config \
            XDG_DATA_HOME=/opt/radegast/home/.local/share \
            XDG_CACHE_HOME=/opt/radegast/home/.cache \
            XDG_STATE_HOME=/opt/radegast/home/.local/state \
            UV_TOOL_DIR=/opt/radegast/home/.local/share/uv/tools \
            UV_TOOL_BIN_DIR=/opt/radegast/home/.local/bin \
            UV_CACHE_DIR=/opt/radegast/home/.cache/uv \
            ${HTTP_PROXY:+HTTP_PROXY="$HTTP_PROXY"} \
            ${HTTPS_PROXY:+HTTPS_PROXY="$HTTPS_PROXY"} \
            ${NO_PROXY:+NO_PROXY="$NO_PROXY"} \
            ${http_proxy:+http_proxy="$http_proxy"} \
            ${https_proxy:+https_proxy="$https_proxy"} \
            ${no_proxy:+no_proxy="$no_proxy"} \
            "$@"
    )
}

# 4. Check/Install uv for radegast-agent user (never use system-wide uv)
echo "Checking if uv is installed for radegast-agent user..."
get_uv_path() {
    if [ -f "/opt/radegast/home/.local/bin/uv" ]; then
        echo "/opt/radegast/home/.local/bin/uv"
        return 0
    fi
    if [ -f "/opt/radegast/home/.cargo/bin/uv" ]; then
        echo "/opt/radegast/home/.cargo/bin/uv"
        return 0
    fi
    return 1
}

UV_BIN=$(get_uv_path || true)
if [ -z "$UV_BIN" ]; then
    echo "uv is not installed for radegast-agent, installing..."
    # Attempt 1: official astral.sh installer (recommended, works on all distros)
    if command -v curl > /dev/null 2>&1; then
        echo "Installing uv via astral.sh installer..."
        run_as_agent sh -c "curl -LsSf https://astral.sh/uv/install.sh | sh"
    else
        # Attempt 2: via pip as last resort
        echo "curl not found, attempting uv install via pip..."
        run_as_agent python3 -m pip install --user --break-system-packages uv || true
    fi
    
    UV_BIN=$(get_uv_path || true)
    if [ -z "$UV_BIN" ]; then
        echo "ERROR: Failed to install uv for radegast-agent." >&2
        exit 1
    fi
else
    echo "uv is already installed for radegast-agent at: $UV_BIN"
    echo "Attempting to upgrade uv to the newest version..."
    run_as_agent "$UV_BIN" self update || echo "Update not available or failed, continuing with current version"
fi
echo "uv found at: $UV_BIN"

# 5. Install radegast-agent via uv
echo "Installing/upgrading radegast-agent tool..."
run_as_agent "$UV_BIN" tool install --upgrade {{ agent_package }}

# Verify agent executable exists
if [ ! -f "/opt/radegast/home/.local/bin/radegast-edr-agent" ]; then
    echo "ERROR: radegast-agent executable not found at /opt/radegast/home/.local/bin/radegast-edr-agent after installation." >&2
    exit 1
fi

# 6. Download and setup rustinel
echo "Downloading rustinel..."
mkdir -p /opt/radegast/rustinel

# Try local download first, then fall back to the official server
if ! curl -sSL -f -o /opt/radegast/rustinel/rustinel.zip "{{ backend_url }}/api/v1/device/agent/download?os=linux&arch=${ARCH_NAME}{{ rustinel_version_query | default('') }}"; then
    echo "Local rustinel download failed, falling back to official console..."
    if ! curl -sSL -f -o /opt/radegast/rustinel/rustinel.zip "https://console-api.radegast.app/api/v1/device/agent/download?os=linux&arch=${ARCH_NAME}{{ rustinel_version_query | default('') }}"; then
        echo "ERROR: Failed to download rustinel from both local and official instances." >&2
        exit 1
    fi
fi
echo "Extracting rustinel..."
unzip -o /opt/radegast/rustinel/rustinel.zip -d /opt/radegast/rustinel
rm -f /opt/radegast/rustinel/rustinel.zip
chmod +x /opt/radegast/rustinel/rustinel
chown -R root:root /opt/radegast/rustinel
chmod 755 /opt/radegast/rustinel
chmod 755 /opt/radegast/rustinel/rustinel

# Setup auto-updater (if rustinel-updater binary was bundled in the archive)
{% if rustinel_autoupdate %}
if [ -f /opt/radegast/rustinel/rustinel-updater ]; then
    echo "Setting up Rustinel auto-updater service..."
    chmod +x /opt/radegast/rustinel/rustinel-updater

    cat << 'EOF' > /etc/systemd/system/rustinel-updater.service
{{ rustinel_updater_service_content }}
EOF
    chmod 644 /etc/systemd/system/rustinel-updater.service
else
    echo "Rustinel auto-updater binary not found in archive, skipping auto-updater setup."
fi
{% endif %}

# 7. Write configs and service files
echo "Writing configuration files..."
cat << 'EOF' > /etc/rustinel/config.toml
{{ config_content }}
EOF
chown root:radegast-agent /etc/rustinel/config.toml
chmod 660 /etc/rustinel/config.toml

echo "Writing uninstall script..."
cat << 'EOF' > /opt/radegast/uninstall.sh
#!/bin/bash
# Radegast EDR Agent & Rustinel Uninstallation Script
if [ "$EUID" -ne 0 ]; then
    echo "ERROR: Please run uninstall script as root." >&2
    exit 1
fi

echo "WARNING: The signing key cannot be changed and must be backed-up manually if moving to another device."
read -p "Have you backed-up your device signing key manually? (y/n): " confirm
if [ "$confirm" != "y" ] && [ "$confirm" != "Y" ]; then
    echo "Uninstallation cancelled."
    exit 1
fi

echo "=== Starting Radegast EDR Agent & Rustinel Uninstallation ==="

systemctl stop radegast-agent || true
systemctl disable radegast-agent || true
systemctl stop rustinel || true
systemctl disable rustinel || true
systemctl stop rustinel-updater || true
systemctl disable rustinel-updater || true

rm -f /etc/systemd/system/radegast-agent.service
rm -f /etc/systemd/system/rustinel.service
rm -f /etc/systemd/system/rustinel-updater.service
systemctl daemon-reload

if id "radegast-agent" >/dev/null 2>&1; then
    userdel -r radegast-agent || true
fi
groupdel radegast-agent 2>/dev/null || true

rm -rf /etc/rustinel
rm -rf /var/log/rustinel
rm -f /etc/sysctl.d/99-radegast-perf.conf

rm -rf /opt/radegast/rustinel
rm -rf /opt/radegast/state
rm -rf /opt/radegast/home

echo "=== Radegast EDR Agent & Rustinel uninstalled successfully ==="

rm -f /opt/radegast/uninstall.sh
rmdir /opt/radegast 2>/dev/null || true
EOF
chmod +x /opt/radegast/uninstall.sh

cat << 'EOF' > /etc/systemd/system/rustinel.service
{{ rustinel_service_content }}
EOF
chmod 644 /etc/systemd/system/rustinel.service

cat << 'EOF' > /etc/systemd/system/radegast-agent.service
{{ radegast_service_content }}
EOF
sed -i "s/%REPLACE_WITH_YOUR_AGENT_TOKEN%/$RADEGAST_TOKEN/g" /etc/systemd/system/radegast-agent.service
chmod 600 /etc/systemd/system/radegast-agent.service

# 8. Start and enable services
echo "Starting and enabling services..."
systemctl daemon-reload

systemctl enable rustinel
systemctl restart rustinel

{% if rustinel_autoupdate %}
if [ -f /opt/radegast/rustinel/rustinel-updater ]; then
    systemctl enable rustinel-updater
    systemctl restart rustinel-updater
fi
{% endif %}

systemctl enable radegast-agent
systemctl restart radegast-agent

# 9. Verify everything is running
echo "Checking service status..."
sleep 2

if ! systemctl is-active --quiet rustinel; then
    echo "ERROR: rustinel service is not running." >&2
    systemctl status rustinel
    exit 1
fi

if ! systemctl is-active --quiet radegast-agent; then
    echo "ERROR: radegast-agent service is not running." >&2
    systemctl status radegast-agent
    exit 1
fi

{% if rustinel_autoupdate %}
if [ -f /opt/radegast/rustinel/rustinel-updater ]; then
    if ! systemctl is-active --quiet rustinel-updater; then
        echo "WARNING: rustinel-updater service is not running." >&2
        systemctl status rustinel-updater || true
    fi
fi
{% endif %}

echo "=== Radegast EDR Agent & Rustinel setup completed successfully ==="
