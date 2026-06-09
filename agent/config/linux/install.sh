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
if ! id "radegast-agent" >/dev/null 2>&1; then
    useradd -r -m -d /opt/radegast/home -s /bin/bash radegast-agent
    chmod 700 /opt/radegast/home
else
    echo "User radegast-agent already exists."
fi

# Setup directories with least privileges
echo "Setting up directories and permissions..."
mkdir -p /etc/rustinel/rules/ioc
touch /etc/rustinel/rules/ioc/hashes.txt
touch /etc/rustinel/rules/ioc/ips.txt
touch /etc/rustinel/rules/ioc/domains.txt
touch /etc/rustinel/rules/ioc/paths_regex.txt
chown -R radegast-agent:root /etc/rustinel/rules
chmod 750 /etc/rustinel/rules
chmod 750 /etc/rustinel/rules/ioc
chmod 640 /etc/rustinel/rules/ioc/*.txt
chown radegast-agent:root /etc/rustinel/
chmod 750 /etc/rustinel/

mkdir -p /var/log/rustinel
chown root:radegast-agent /var/log/rustinel
chmod 750 /var/log/rustinel

mkdir -p /opt/radegast/home
chown radegast-agent:radegast-agent /opt/radegast/home
chmod 700 /opt/radegast/home

mkdir -p /opt/radegast/state
chown radegast-agent:radegast-agent /opt/radegast/state
chmod 700 /opt/radegast/state

# 4. Check/Install uv
echo "Checking if uv is installed..."
get_uv_path() {
    if command -v uv >/dev/null 2>&1; then
        command -v uv
        return 0
    fi
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
        sudo -u radegast-agent -i sh -c "curl -LsSf https://astral.sh/uv/install.sh | sh"
    else
        # Attempt 2: via pip as last resort
        echo "curl not found, attempting uv install via pip..."
        sudo -u radegast-agent -i python3 -m pip install --user --break-system-packages uv || true
    fi
    
    UV_BIN=$(get_uv_path || true)
    if [ -z "$UV_BIN" ]; then
        echo "ERROR: Failed to install uv." >&2
        exit 1
    fi
fi
echo "uv found at: $UV_BIN"

# 5. Install radegast-agent via uv
echo "Installing radegast-agent tool..."
sudo -u radegast-agent -i "$UV_BIN" tool install radegast-edr-agent

# Verify agent executable exists
if [ ! -f "/opt/radegast/home/.local/bin/radegast-edr-agent" ]; then
    echo "ERROR: radegast-agent executable not found at /opt/radegast/home/.local/bin/radegast-edr-agent after installation." >&2
    exit 1
fi

# 6. Download and setup rustinel
echo "Downloading rustinel..."
mkdir -p /opt/radegast/rustinel
curl -sSL -o /opt/radegast/rustinel/rustinel.zip "{{ backend_url }}/api/v1/device/agent/download?os=linux&arch=${ARCH_NAME}"
echo "Extracting rustinel..."
unzip -o /opt/radegast/rustinel/rustinel.zip -d /opt/radegast/rustinel
rm -f /opt/radegast/rustinel/rustinel.zip
chmod +x /opt/radegast/rustinel/rustinel
chown -R root:root /opt/radegast/rustinel
chmod 755 /opt/radegast/rustinel
chmod 755 /opt/radegast/rustinel/rustinel

# 7. Write configs and service files
echo "Writing configuration files..."
cat << 'EOF' > /opt/radegast/rustinel/config.toml
{{ config_content }}
EOF
chmod 644 /opt/radegast/rustinel/config.toml

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

rm -f /etc/systemd/system/radegast-agent.service
rm -f /etc/systemd/system/rustinel.service
systemctl daemon-reload

if id "radegast-agent" >/dev/null 2>&1; then
    userdel -r radegast-agent || true
fi

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

echo "=== Radegast EDR Agent & Rustinel setup completed successfully ==="
