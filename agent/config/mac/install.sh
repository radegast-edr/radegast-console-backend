#!/bin/bash
# Radegast EDR Agent & Rustinel macOS Auto-installation Script
set -e

echo "=== Starting Radegast EDR Agent & Rustinel macOS Installation ==="

# 0. Check RADEGAST_TOKEN environment variable
if [ -z "$RADEGAST_TOKEN" ]; then
    echo "ERROR: RADEGAST_TOKEN environment variable is not set." >&2
    echo "Please run: curl ... | sudo RADEGAST_TOKEN=\"your_token\" sh" >&2
    exit 1
fi

# 1. Verify required platform and commands
if [ "$(uname -s)" != "Darwin" ]; then
    echo "ERROR: This install script is only for macOS." >&2
    exit 1
fi

if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: This script must be run as root (use sudo)." >&2
    exit 1
fi

MACOS_VERSION=$(sw_vers -productVersion | cut -d. -f1)
if [ "$MACOS_VERSION" -lt 11 ]; then
    echo "ERROR: macOS 11 (Big Sur) or later is required." >&2
    exit 1
fi

if ! command -v curl >/dev/null 2>&1; then
    echo "ERROR: curl is required but not installed." >&2
    exit 1
fi

# Ensure Xcode Command Line Tools are installed (provides install_name_tool for dynamic library relocation)
if ! xcode-select -p >/dev/null 2>&1; then
    echo "Xcode Command Line Tools not found. Installing headlessly via softwareupdate..."
    CLT_PLACEHOLDER="/tmp/.com.apple.dt.CommandLineTools.installondemand.in-progress"
    touch "$CLT_PLACEHOLDER"
    CLT_PKG=$(softwareupdate -l 2>/dev/null | grep -E "\*.*Command Line Tools" | head -n 1 | awk -F"*" '{print $2}' | sed -e 's/^ *//' | sed -e 's/Label: //' | tr -d '\n')
    if [ -n "$CLT_PKG" ]; then
        echo "Installing $CLT_PKG..."
        softwareupdate -i "$CLT_PKG" --verbose || true
    else
        echo "Requesting xcode-select install..."
        xcode-select --install 2>/dev/null || true
    fi
    rm -f "$CLT_PLACEHOLDER"
fi

# Ensure pkg-config is available via Homebrew (needed for packages like cryptography)
get_brew_bin() {
    if [ -f "/opt/homebrew/bin/brew" ]; then
        echo "/opt/homebrew/bin/brew"
        return 0
    elif [ -f "/usr/local/bin/brew" ]; then
        echo "/usr/local/bin/brew"
        return 0
    elif command -v brew >/dev/null 2>&1; then
        command -v brew
        return 0
    fi
    return 1
}

get_user_home() {
    local u="$1"
    local h=""
    h=$(dscl . -read "/Users/$u" NFSHomeDirectory 2>/dev/null | awk '{print $2}')
    if [ -z "$h" ]; then
        h=$(getent passwd "$u" 2>/dev/null | cut -d: -f6)
    fi
    if [ -z "$h" ]; then
        h="/Users/$u"
    fi
    echo "$h"
}

export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
BREW_BIN=$(get_brew_bin || true)
OPENSSL_PREFIX=""
if [ -n "$BREW_BIN" ]; then
    OPENSSL_PREFIX=$("$BREW_BIN" --prefix openssl@3 2>/dev/null || true)
fi

if ! command -v pkg-config >/dev/null 2>&1 || [ -z "$OPENSSL_PREFIX" ] || [ ! -d "$OPENSSL_PREFIX" ]; then
    echo "pkg-config or openssl@3 not found. Checking for Homebrew..."
    if [ -z "$BREW_BIN" ]; then
        echo "Installing Homebrew directly..."
        ARCH=$(uname -m)
        if [ "$ARCH" = "arm64" ]; then
            BREW_PREFIX="/opt/homebrew"
            mkdir -p "$BREW_PREFIX"
            curl -fsSL https://github.com/Homebrew/brew/tarball/master | tar xz --strip-components 1 -C "$BREW_PREFIX"
            mkdir -p "$BREW_PREFIX"/{bin,etc,include,lib,sbin,share,var,opt,Cellar,Frameworks,Caskroom}
            mkdir -p "$BREW_PREFIX"/var/homebrew/{locks,linked,tab}
            mkdir -p "$BREW_PREFIX"/share/{doc,man,man1,zsh,zsh/site-functions}
            mkdir -p "$BREW_PREFIX"/lib/pkgconfig
            BREW_BIN="/opt/homebrew/bin/brew"
        else
            BREW_PREFIX="/usr/local/Homebrew"
            mkdir -p "$BREW_PREFIX"
            curl -fsSL https://github.com/Homebrew/brew/tarball/master | tar xz --strip-components 1 -C "$BREW_PREFIX"
            mkdir -p /usr/local/{bin,etc,include,lib,sbin,share,var,opt,Cellar,Frameworks,Caskroom}
            mkdir -p /usr/local/var/homebrew/{locks,linked,tab}
            mkdir -p /usr/local/share/{doc,man,man1,zsh,zsh/site-functions}
            mkdir -p /usr/local/lib/pkgconfig
            ln -sf "$BREW_PREFIX/bin/brew" /usr/local/bin/brew
            BREW_BIN="/usr/local/bin/brew"
        fi
    fi

    # Fix permissions on all Homebrew directories, user caches, and temp directories so brew never encounters Permission Denied
    if [ -n "$SUDO_USER" ] && [ "$SUDO_USER" != "root" ]; then
        USER_HOME=$(get_user_home "$SUDO_USER")
        mkdir -p "$USER_HOME/Library/Caches/Homebrew" "$USER_HOME/Library/Logs/Homebrew" "/tmp/homebrew-$SUDO_USER" "/Library/Caches/Homebrew"
        chown -R "$SUDO_USER" "$USER_HOME/Library/Caches/Homebrew" "$USER_HOME/Library/Logs/Homebrew" "/tmp/homebrew-$SUDO_USER" "/Library/Caches/Homebrew" 2>/dev/null || true
        chmod -R 777 "$USER_HOME/Library/Caches/Homebrew" "$USER_HOME/Library/Logs/Homebrew" "/tmp/homebrew-$SUDO_USER" "/Library/Caches/Homebrew" 2>/dev/null || true
        if [ -d "/opt/homebrew" ]; then
            mkdir -p /opt/homebrew/{bin,etc,include,lib,sbin,share,var,opt,Cellar,Frameworks,Caskroom}
            mkdir -p /opt/homebrew/var/homebrew/{locks,linked,tab}
            mkdir -p /opt/homebrew/lib/pkgconfig
            chown -R "$SUDO_USER" "/opt/homebrew" 2>/dev/null || true
            chmod -R 777 "/opt/homebrew" 2>/dev/null || true
        fi
        if [ -d "/usr/local/Homebrew" ]; then
            mkdir -p /usr/local/{bin,etc,include,lib,sbin,share,var,opt,Cellar,Frameworks,Caskroom}
            mkdir -p /usr/local/var/homebrew/{locks,linked,tab}
            mkdir -p /usr/local/lib/pkgconfig
            chown -R "$SUDO_USER" /usr/local/Homebrew /usr/local/{bin,etc,include,lib,sbin,share,var,opt,Cellar,Frameworks,Caskroom} 2>/dev/null || true
            chmod -R 777 /usr/local/Homebrew /usr/local/{bin,etc,include,lib,sbin,share,var,opt,Cellar,Frameworks,Caskroom} 2>/dev/null || true
        fi
    fi

    if [ -n "$BREW_BIN" ] && [ -x "$BREW_BIN" ]; then
        echo "Installing pkg-config and openssl@3 using Homebrew ($BREW_BIN)..."
        if [ -n "$SUDO_USER" ] && [ "$SUDO_USER" != "root" ]; then
            USER_HOME=$(get_user_home "$SUDO_USER")
            sudo -u "$SUDO_USER" -H env \
                HOME="$USER_HOME" \
                USER="$SUDO_USER" \
                LOGNAME="$SUDO_USER" \
                PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin" \
                HOMEBREW_CACHE="$USER_HOME/Library/Caches/Homebrew" \
                HOMEBREW_LOGS="$USER_HOME/Library/Logs/Homebrew" \
                HOMEBREW_TEMP="/tmp/homebrew-$SUDO_USER" \
                HOMEBREW_NO_ANALYTICS=1 \
                HOMEBREW_NO_AUTO_UPDATE=1 \
                HOMEBREW_NO_INSTALL_CLEANUP=1 \
                "$BREW_BIN" install pkg-config openssl@3 || true
        else
            HOMEBREW_NO_ANALYTICS=1 HOMEBREW_NO_AUTO_UPDATE=1 HOMEBREW_NO_INSTALL_CLEANUP=1 "$BREW_BIN" install pkg-config openssl@3 || true
        fi
    fi
fi

export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
OPENSSL_PREFIX=""
if [ -n "$BREW_BIN" ]; then
    OPENSSL_PREFIX=$("$BREW_BIN" --prefix openssl@3 2>/dev/null || true)
fi
if [ -z "$OPENSSL_PREFIX" ]; then
    OPENSSL_PREFIX=$(/opt/homebrew/bin/brew --prefix openssl@3 2>/dev/null || /usr/local/bin/brew --prefix openssl@3 2>/dev/null || true)
fi

BASE_PKG_CONFIG="/opt/homebrew/lib/pkgconfig:/usr/local/lib/pkgconfig:/opt/homebrew/share/pkgconfig:/usr/local/share/pkgconfig"
if [ -n "$OPENSSL_PREFIX" ] && [ -d "$OPENSSL_PREFIX" ]; then
    export OPENSSL_DIR="$OPENSSL_PREFIX"
    export OPENSSL_INCLUDE_DIR="${OPENSSL_PREFIX}/include"
    export OPENSSL_LIB_DIR="${OPENSSL_PREFIX}/lib"
    export LDFLAGS="-L${OPENSSL_PREFIX}/lib ${LDFLAGS:-}"
    export CPPFLAGS="-I${OPENSSL_PREFIX}/include ${CPPFLAGS:-}"
    if [ -n "${PKG_CONFIG_PATH:-}" ]; then
        export PKG_CONFIG_PATH="${OPENSSL_PREFIX}/lib/pkgconfig:${BASE_PKG_CONFIG}:${PKG_CONFIG_PATH}"
    else
        export PKG_CONFIG_PATH="${OPENSSL_PREFIX}/lib/pkgconfig:${BASE_PKG_CONFIG}"
    fi
else
    if [ -n "${PKG_CONFIG_PATH:-}" ]; then
        export PKG_CONFIG_PATH="/usr/local/opt/openssl@3/lib/pkgconfig:/opt/homebrew/opt/openssl@3/lib/pkgconfig:${BASE_PKG_CONFIG}:${PKG_CONFIG_PATH}"
    else
        export PKG_CONFIG_PATH="/usr/local/opt/openssl@3/lib/pkgconfig:/opt/homebrew/opt/openssl@3/lib/pkgconfig:${BASE_PKG_CONFIG}"
    fi
fi

# 2. Create _radegast system user (macOS convention for service accounts)
echo "Creating _radegast system user..."
if ! dscl . -read /Users/_radegast >/dev/null 2>&1; then
    # Find available UID under 500
    LAST_UID=$(dscl . -list /Users UniqueID | awk '$2 < 500 {print $2}' | sort -n | tail -1)
    if [ -z "$LAST_UID" ]; then
        NEW_UID=450
    else
        NEW_UID=$((LAST_UID + 1))
    fi
    dscl . -create /Users/_radegast
    dscl . -create /Users/_radegast UserShell /bin/zsh
    dscl . -create /Users/_radegast RealName "Radegast EDR Agent"
    dscl . -create /Users/_radegast UniqueID "$NEW_UID"
    dscl . -create /Users/_radegast PrimaryGroupID 20
    dscl . -create /Users/_radegast NFSHomeDirectory /Library/Radegast/home
    dscl . -create /Users/_radegast IsHidden 1
else
    echo "User _radegast already exists."
    dscl . -create /Users/_radegast UserShell /bin/zsh 2>/dev/null || true
fi

# 3. Create directory layout with appropriate permissions
RADEGAST_DIR="/Library/Radegast"
LOG_DIR="/Library/Logs/Radegast"

echo "Setting up directories and permissions..."
mkdir -p "$RADEGAST_DIR"/home
mkdir -p "$RADEGAST_DIR"/rustinel
mkdir -p "$RADEGAST_DIR"/etc/rules/sigma
mkdir -p "$RADEGAST_DIR"/etc/rules/yara
mkdir -p "$RADEGAST_DIR"/etc/rules/ioc
mkdir -p "$RADEGAST_DIR"/state
mkdir -p "$LOG_DIR"

touch "$RADEGAST_DIR"/etc/rules/ioc/hashes.txt
touch "$RADEGAST_DIR"/etc/rules/ioc/ips.txt
touch "$RADEGAST_DIR"/etc/rules/ioc/domains.txt
touch "$RADEGAST_DIR"/etc/rules/ioc/paths_regex.txt

touch "$LOG_DIR"/radegast-agent-stdout.log
touch "$LOG_DIR"/radegast-agent-stderr.log
touch "$LOG_DIR"/rustinel-stdout.log
touch "$LOG_DIR"/rustinel-stderr.log
touch "$LOG_DIR"/alerts.json

chown -R _radegast:wheel "$LOG_DIR" 2>/dev/null || chown -R _radegast:staff "$LOG_DIR" 2>/dev/null || chown -R _radegast "$LOG_DIR"
chmod 775 "$LOG_DIR"
chmod 664 "$LOG_DIR"/*.log "$LOG_DIR"/alerts.json 2>/dev/null || true

chown -R _radegast:wheel "$RADEGAST_DIR"/etc
chmod -R 775 "$RADEGAST_DIR"/etc
chmod 640 "$RADEGAST_DIR"/etc/rules/ioc/*.txt

chown -R _radegast:staff "$RADEGAST_DIR"/home 2>/dev/null || chown -R _radegast "$RADEGAST_DIR"/home
chmod 700 "$RADEGAST_DIR"/home

chown -R _radegast:staff "$RADEGAST_DIR"/state 2>/dev/null || chown -R _radegast "$RADEGAST_DIR"/state
chmod 700 "$RADEGAST_DIR"/state

# 4. Check/Install uv for _radegast user (never use system-wide uv)
echo "Checking if uv is installed for _radegast user..."
get_uv_path() {
    if [ -f "/Library/Radegast/home/.local/bin/uv" ]; then
        echo "/Library/Radegast/home/.local/bin/uv"
        return 0
    fi
    if [ -f "/Library/Radegast/home/.cargo/bin/uv" ]; then
        echo "/Library/Radegast/home/.cargo/bin/uv"
        return 0
    fi
    return 1
}

UV_BIN=$(get_uv_path || true)
if [ -z "$UV_BIN" ]; then
    echo "uv is not installed for _radegast, installing..."
    curl -LsSf https://astral.sh/uv/install.sh | sudo -u _radegast -H env HOME=/Library/Radegast/home sh
    UV_BIN=$(get_uv_path || true)
    if [ -z "$UV_BIN" ]; then
        echo "ERROR: Failed to install uv for _radegast." >&2
        exit 1
    fi
else
    echo "uv is already installed for _radegast at: $UV_BIN"
    sudo -u _radegast -H env HOME=/Library/Radegast/home "$UV_BIN" self update || echo "Update not available or failed"
fi

# 5. Install Python runtime and radegast-agent via uv
echo "Installing Python runtime and radegast-agent via uv..."
sudo -u _radegast -H env \
    HOME=/Library/Radegast/home \
    PATH="/Library/Radegast/home/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH" \
    OPENSSL_DIR="${OPENSSL_DIR:-}" \
    OPENSSL_INCLUDE_DIR="${OPENSSL_INCLUDE_DIR:-}" \
    OPENSSL_LIB_DIR="${OPENSSL_LIB_DIR:-}" \
    PKG_CONFIG_PATH="$PKG_CONFIG_PATH" \
    LDFLAGS="${LDFLAGS:-}" \
    CPPFLAGS="${CPPFLAGS:-}" \
    "$UV_BIN" python install 3.13

sudo -u _radegast -H env \
    HOME=/Library/Radegast/home \
    PATH="/Library/Radegast/home/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH" \
    OPENSSL_DIR="${OPENSSL_DIR:-}" \
    OPENSSL_INCLUDE_DIR="${OPENSSL_INCLUDE_DIR:-}" \
    OPENSSL_LIB_DIR="${OPENSSL_LIB_DIR:-}" \
    PKG_CONFIG_PATH="$PKG_CONFIG_PATH" \
    LDFLAGS="${LDFLAGS:-}" \
    CPPFLAGS="${CPPFLAGS:-}" \
    "$UV_BIN" tool install --python 3.13 --upgrade {{ agent_package }}

# Verify agent executable exists
if [ ! -f "/Library/Radegast/home/.local/bin/radegast-edr-agent" ]; then
    echo "ERROR: radegast-agent executable not found at /Library/Radegast/home/.local/bin/radegast-edr-agent after installation." >&2
    exit 1
fi

# 6. Download and setup rustinel
echo "Downloading rustinel..."
ARCH=$(uname -m)
if [ "$ARCH" = "arm64" ] || [ "$ARCH" = "aarch64" ]; then
    ARCH_NAME="m5"
elif [ "$ARCH" = "x86_64" ] || [ "$ARCH" = "amd64" ]; then
    ARCH_NAME="amd64"
else
    ARCH_NAME="$ARCH"
fi

BACKEND_URL="${RADEGAST_BACKEND_URL:-{{ backend_url }}}"
mkdir -p "$RADEGAST_DIR"/rustinel

# Try local download first, then fall back to the official server
if ! curl -sSL -f -o "$RADEGAST_DIR"/rustinel/rustinel.zip "${BACKEND_URL}/api/v1/device/agent/download?os=mac&arch=${ARCH_NAME}"; then
    echo "Local rustinel download failed, falling back to official console..."
    if ! curl -sSL -f -o "$RADEGAST_DIR"/rustinel/rustinel.zip "https://console-api.radegast.app/api/v1/device/agent/download?os=mac&arch=${ARCH_NAME}"; then
        echo "ERROR: Failed to download rustinel for macOS (${ARCH_NAME}) from local and official instances." >&2
        echo "Please upload a macOS release for ${ARCH_NAME} in the Releases management section." >&2
        exit 1
    fi
fi
echo "Extracting rustinel..."
unzip -o "$RADEGAST_DIR"/rustinel/rustinel.zip -d "$RADEGAST_DIR"/rustinel
rm -f "$RADEGAST_DIR"/rustinel/rustinel.zip

# If the archive contained a nested directory with Rustinel.app, move it to $RADEGAST_DIR/rustinel
if [ ! -d "$RADEGAST_DIR/rustinel/Rustinel.app" ]; then
    NESTED_APP=$(find "$RADEGAST_DIR/rustinel" -maxdepth 2 -type d -name "Rustinel.app" | head -n 1)
    if [ -n "$NESTED_APP" ] && [ "$NESTED_APP" != "$RADEGAST_DIR/rustinel/Rustinel.app" ]; then
        mv "$NESTED_APP" "$RADEGAST_DIR/rustinel/Rustinel.app"
    fi
fi

# Ensure executable permissions and symlink to Rustinel.app binary
if [ -d "$RADEGAST_DIR/rustinel/Rustinel.app" ]; then
    APP_BIN="$RADEGAST_DIR/rustinel/Rustinel.app/Contents/MacOS/rustinel"
    if [ -f "$APP_BIN" ]; then
        chmod +x "$APP_BIN"
        ln -sf "$APP_BIN" "$RADEGAST_DIR/rustinel/rustinel"
    fi
elif [ -f "$RADEGAST_DIR/rustinel/rustinel" ]; then
    chmod +x "$RADEGAST_DIR/rustinel/rustinel"
fi

chown -R root:wheel "$RADEGAST_DIR"/rustinel
chmod -R 755 "$RADEGAST_DIR"/rustinel
xattr -dr com.apple.quarantine "$RADEGAST_DIR"/rustinel 2>/dev/null || true

# 7. Write configs and LaunchDaemon plists
echo "Writing configuration files..."
cat << 'EOF' > "$RADEGAST_DIR"/etc/config.toml
{{ config_content }}
EOF
chown _radegast:wheel "$RADEGAST_DIR"/etc/config.toml
chmod 640 "$RADEGAST_DIR"/etc/config.toml

echo "Writing uninstall script..."
cat << 'EOF' > "$RADEGAST_DIR"/uninstall.sh
#!/bin/bash
# Radegast EDR Agent & Rustinel macOS Uninstallation Script
if [ "$(id -u)" -ne 0 ]; then
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

launchctl unload /Library/LaunchDaemons/app.radegast.agent.plist 2>/dev/null || true
launchctl unload /Library/LaunchDaemons/io.rustinel.daemon.plist 2>/dev/null || true

rm -f /Library/LaunchDaemons/app.radegast.agent.plist
rm -f /Library/LaunchDaemons/io.rustinel.daemon.plist

if dscl . -read /Users/_radegast >/dev/null 2>&1; then
    dscl . -delete /Users/_radegast || true
fi

rm -rf /Library/Radegast
rm -rf /Library/Logs/Radegast

echo "=== Radegast EDR Agent & Rustinel uninstalled successfully ==="
EOF
chmod +x "$RADEGAST_DIR"/uninstall.sh

cat << 'EOF' > /Library/LaunchDaemons/io.rustinel.daemon.plist
{{ rustinel_plist_content }}
EOF
chmod 644 /Library/LaunchDaemons/io.rustinel.daemon.plist

cat << 'EOF' > /Library/LaunchDaemons/app.radegast.agent.plist
{{ radegast_plist_content }}
EOF
sed -i '' "s|%REPLACE_WITH_YOUR_AGENT_TOKEN%|${RADEGAST_TOKEN}|g" /Library/LaunchDaemons/app.radegast.agent.plist 2>/dev/null || sed -i "s|%REPLACE_WITH_YOUR_AGENT_TOKEN%|${RADEGAST_TOKEN}|g" /Library/LaunchDaemons/app.radegast.agent.plist
if [ -n "$RADEGAST_BACKEND_URL" ]; then
    sed -i '' "s|{{ backend_url }}|${RADEGAST_BACKEND_URL}|g" /Library/LaunchDaemons/app.radegast.agent.plist 2>/dev/null || sed -i "s|{{ backend_url }}|${RADEGAST_BACKEND_URL}|g" /Library/LaunchDaemons/app.radegast.agent.plist 2>/dev/null || true
fi
chmod 600 /Library/LaunchDaemons/app.radegast.agent.plist

# 8. Start LaunchDaemons
echo "Loading and starting services..."
launchctl unload -w /Library/LaunchDaemons/io.rustinel.daemon.plist 2>/dev/null || true
launchctl unload -w /Library/LaunchDaemons/app.radegast.agent.plist 2>/dev/null || true

launchctl load -w /Library/LaunchDaemons/io.rustinel.daemon.plist 2>/dev/null || true
launchctl load -w /Library/LaunchDaemons/app.radegast.agent.plist 2>/dev/null || true

echo ""
echo "=== Radegast EDR Agent & Rustinel setup completed ==="
echo ""
echo "IMPORTANT POST-INSTALLATION STEP FOR MACOS:"
echo "Grant Full Disk Access to Rustinel to enable telemetry collection:"
echo "  1. Open System Settings -> Privacy & Security -> Full Disk Access"
echo "  2. Grant access to /Library/Radegast/rustinel/Rustinel.app (or /Library/Radegast/rustinel/rustinel)"
echo "  3. Restart Rustinel service if needed: sudo launchctl kickstart -k system/io.rustinel.daemon"
echo ""
