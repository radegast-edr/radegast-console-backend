# API Keys

## Overview

API Keys provide programmatic authentication to the Radegast EDR API. You can use API keys for automation scripts, SIEM integrations, and reporting pipelines. Each key enforces specific permission scopes that restrict allowed API operations.

## Capabilities

- **Scripted Automation**: Automate platform management using REST API endpoints.
- **SIEM Integration**: Stream events and alerts into external security data lakes.
- **Custom Reporting**: Extract metrics and device statuses programmatically.
- **Granular Scopes**: Restrict each API key to the minimum necessary permissions.
- **Auditing and Expiration**: Track the last used date and configure optional key expiration times.

## Prerequisites

Before you create an API key, make sure that:
1. Your account has **API Keys Support** enabled in Settings.
2. You completed email verification.
3. Your account has initialized encryption keys.

## Enable API Keys for Your Account

1. Open **Settings > Security**.
2. Locate the **API Keys Support** section.
3. Toggle **Enable API Keys Support** to ON.
4. Click **Save**.

:::{note}
If the toggle does not appear, contact your system administrator to enable API key creation for your user role.
:::

## View API Keys

1. Log in to the Radegast Console.
2. Click **API Keys** in the main navigation menu or under Settings.
3. The page displays all active API keys.

![API Keys Management](../_static/screenshots/apikeys-view.png)

The table displays:
- **Name**: Assigned description of the key.
- **Prefix**: First 12 characters of the key string.
- **Scopes**: Granted permission scopes.
- **Created**: Generation timestamp.
- **Last Used**: Timestamp of the most recent API request.
- **Expires**: Expiration date, or None.
- **Actions**: Buttons to inspect details or revoke the key.

## Create an API Key

1. Click **Create API Key** on the API Keys page.
2. Complete the configuration form:
   - **Name**: Enter a descriptive name (for example, `Splunk Forwarder`).
   - **Scopes**: Select the necessary permissions:
     - `devices`: Read, create, update, and delete endpoints.
     - `teams`: Read team details and manage team memberships.
     - `groups`: Read and configure device groups.
     - `packs`: Read and upload detection packs.
     - `logs`: Read event logs and update alert triage statuses.
     - `user`: Inspect user details, configure preferences, and manage encryption keys.
     - `releases` (Admins only): Upload, download, and delete agent release binaries.
   - **Expiration** (Optional): Specify an expiration date.
3. Click **Create Key**.
4. The dialog displays the **full API key string** (starts with `rg_`).
   - Copy and store this secret in a secure credential store immediately.
   - The console never displays the full key string again.
5. Click **Done** to close the dialog.

:::{caution}
If you lose an API key string, you cannot recover it. You must delete the key and generate a replacement.
:::

## Authenticate with an API Key

Include the API key in the `Authorization` header of each HTTP request:

```bash
# Example curl request
curl -X GET "https://your-radegast-server.com/api/v1/logs/" \
  -H "Authorization: Bearer rg_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
```

To review interactive API endpoints, open `https://<console-url>/docs`.

## Permission Scopes Reference

| Scope | Allowed Operations |
|:---|:---|
| **devices** | List devices, register new endpoints, update metadata, and delete devices. |
| **teams** | List teams, inspect team memberships, and manage team permissions. |
| **groups** | List device groups, create groups, and modify group memberships. |
| **packs** | List detection packs, download versions, and upload new rule packages. |
| **logs** | Query event logs and alerts, set alert resolutions, and mark alerts as seen. |
| **user** | Inspect user details, configure notification settings, and manage encryption keys. |
| **releases** | Upload, download, and delete agent release packages (Admin accounts only). |

:::{tip}
Apply the principle of least privilege. Grant each API key only the scopes required for its task.
:::

## Create Encryption Keys with the API

You can use an API key with the `user:create` permission to add a public key to your account.

First, generate an age keypair with `age-keygen`:
```bash
age-keygen -o key.txt
```
This command writes your private key to `key.txt` and outputs your public key.

Next, send a request to register the public key:
```bash
PUBLIC_KEY="age1..."
API_KEY="rg_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
SERVER_URL="https://your-radegast-server.com"

curl -X POST "$SERVER_URL/api/v1/user/keys" \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d "{\"public_key\": \"$PUBLIC_KEY\", \"name\": \"Automation Key\"}"
```
The server adds the public key to your account.

## Decrypt Logs with age, curl, and jq

Endpoints encrypt all telemetry logs with age before transmission. You can fetch and decrypt the 10 newest logs from the command line using `curl`, `jq`, and [age](https://age-encryption.org).

### Prerequisites
- Install `curl`, `jq`, and `age`.
- Prepare an API key with the `logs:read` scope. If the key file does not exist, the key also requires the `user:create` scope.
- Set the `RADEGAST_API_KEY` environment variable.
- (Optional) Set `RADEGAST_SERVER_URL` (default: `https://console-api.radegast.app/`) and `AGE_KEY_FILE` (default: `key.txt`).

### Procedure
Run this bash script to fetch and decrypt the 10 newest logs:

```bash
#!/usr/bin/env bash
set -euo pipefail

# Read configuration from environment variables
SERVER_URL="${RADEGAST_SERVER_URL:-https://console-api.radegast.app/}"
SERVER_URL="${SERVER_URL%/}"
API_KEY="${RADEGAST_API_KEY:?Error: Set the RADEGAST_API_KEY environment variable}"
IDENTITY_FILE="${AGE_KEY_FILE:-key.txt}"

# Generate and register a new age keypair if the file does not exist
if [ ! -f "$IDENTITY_FILE" ]; then
  echo "Key file '$IDENTITY_FILE' not found. Generating a new age keypair..."
  age-keygen -o "$IDENTITY_FILE"
  chmod 600 "$IDENTITY_FILE"

  PUBLIC_KEY=$(age-keygen -y "$IDENTITY_FILE")
  echo "Registering public key with backend: $PUBLIC_KEY"

  curl -s -f -X POST "${SERVER_URL}/api/v1/user/keys" \
    -H "Authorization: Bearer $API_KEY" \
    -H "Content-Type: application/json" \
    -d "{\"public_key\": \"$PUBLIC_KEY\", \"name\": \"CLI Automation Key\"}"
  echo ""
fi

# Fetch the 10 newest logs and decrypt each log payload
curl -s -f -X GET "${SERVER_URL}/api/v1/logs/?limit=10" \
  -H "Authorization: Bearer $API_KEY" | \
  jq -c '.[]' | \
  while read -r log; do
    log_id=$(echo "$log" | jq -r '.id')
    device_id=$(echo "$log" | jq -r '.device_id')
    timestamp=$(echo "$log" | jq -r '.time')

    echo "=================================================="
    echo "Log ID: $log_id | Device ID: $device_id | Time: $timestamp"
    echo "=================================================="

    # Extract the age-encrypted ciphertext and decrypt with age
    echo "$log" | jq -r '.content' | age -d -i "$IDENTITY_FILE"
    echo ""
  done
```

:::{caution}
A newly registered age key can only decrypt new logs sent after key registration. Endpoints encrypt older logs with previous keys. If you lost your local private key, use your recovery key to recover the private key.
:::

## Recover a Private Key with the Recovery Key

During account setup, the web console provides a 64-character hexadecimal AES recovery key. The server stores an encrypted backup of your age private key.

You can use your recovery key to fetch and decrypt this private key.

### Prerequisites
- Prepare an API key with the `user:read` scope.
- Install [uv](https://docs.astral.sh/uv/) (or Python 3 with `cryptography` and `requests`).
- Have your 64-character hexadecimal recovery AES key available.

### Procedure
Save this script as `recover_key.py`:

```python
#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "cryptography==43.0.3",
#   "requests==2.32.3",
# ]
# ///
"""Recover an age private key using a Radegast recovery AES key."""

import json
import os
import sys
import getpass

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
import requests

server_url = os.environ.get("RADEGAST_SERVER_URL", "https://console-api.radegast.app/").rstrip("/")
api_key = os.environ.get("RADEGAST_API_KEY")
recovery_key = os.environ.get("RADEGAST_RECOVERY_KEY")
output_file = os.environ.get("AGE_KEY_FILE", "key.txt")

if not api_key:
    sys.exit("Error: Set the RADEGAST_API_KEY environment variable.")

if not recovery_key:
    recovery_key = getpass.getpass("Enter your 64-character hex recovery AES key: ").strip()

try:
    aesgcm = AESGCM(bytes.fromhex(recovery_key))
except Exception as e:
    sys.exit(f"Error: Invalid recovery key format: {e}")

try:
    resp = requests.get(
        f"{server_url}/api/v1/user/keys/recover",
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=30,
    )
    resp.raise_for_status()
    keys = resp.json()
except Exception as e:
    sys.exit(f"Error: Failed to fetch recovery keys from server: {e}")

recovered_key = None
for k in keys:
    enc_data = json.loads(k["encrypted_private_key"])
    iv = bytes.fromhex(enc_data["iv"])
    ciphertext = bytes.fromhex(enc_data["ciphertext"])
    try:
        decrypted = aesgcm.decrypt(iv, ciphertext, None).decode("utf-8")
        recovered_key = decrypted
        print(f"Decrypted private key for public key: {k['public_key']}")
        break
    except Exception:
        continue

if not recovered_key:
    sys.exit("Error: Decryption failed. Verify your recovery key.")

with open(output_file, "w") as f:
    f.write(recovered_key.strip() + "\n")

os.chmod(output_file, 0o600)
print(f"Private key saved to {output_file}")
```

Make the script executable and run it:

```bash
export RADEGAST_API_KEY="rg_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
export RADEGAST_RECOVERY_KEY="your_64_character_hex_recovery_key"
chmod +x recover_key.py
./recover_key.py
```

## Delete an API Key

1. Select the key from the API Keys list.
2. Click **Delete** or **Revoke**.
3. Confirm the deletion.
4. The system invalidates the key immediately.

:::{caution}
All automation scripts and SIEM forwarders using this key fail immediately after deletion.
:::

## Troubleshooting

### Cannot Create an API Key

- **Feature Disabled**: Enable **API Keys Support** in user settings.
- **Unverified Account**: Verify your account email address.
- **Role Restrictions**: Ask an administrator to grant API key privileges to your user role.

### Requests Return 401 Unauthorized

- **Invalid Token**: Verify that you passed the token with the `Bearer ` prefix.
- **Key Expired**: Check if the key reached its configured expiration date.
- **Key Revoked**: Verify that another administrator did not delete the key.

### Requests Return 403 Forbidden

- **Missing Scope**: The requested API endpoint requires a scope that was not granted to the key.
- **Team Permissions**: The API key operates with your user account permissions. Verify that your user account has access to the target team or group.
