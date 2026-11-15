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

:::{tip}
Apply the principle of least privilege. Grant each API key only the scopes required for its task.
:::

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
