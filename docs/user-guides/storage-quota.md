# Storage Quota & Space Usage

## Feature Overview

Radegast EDR tracks storage consumption hierarchically across devices, device groups, teams, and users. This enables organizations to monitor telemetry volume, plan infrastructure capacity, and enforce multi-tenant quotas.

Space usage is calculated automatically in the background by a dedicated worker process, ensuring console responsiveness while keeping storage metrics up-to-date across all views.

---

## How Space Usage is Computed

The storage calculation follows a hierarchical rollup model with deduplication:

```
[ Individual Devices ]
        │
        ▼ (Sum of assigned devices)
[ Device Groups ]
        │
        ▼ (Aggregated across groups in team hierarchy)
[ Teams (Root Only) ]  <─── (Managed sub-teams roll up to Managing Team)
        │
        ▼ (Aggregated across qualifying root teams)
[ User Quota ]
```

### 1. Device Space Usage

Every enrolled device reports the disk space consumed by its telemetry logs, detection events, and encrypted communication buffers stored in the backend database. This is represented by `total_space_used` on the device record.

### 2. Device Group Space Usage

Device groups allow you to organize devices by environment, department, or operating system.

- **Computation Rule**: The total space used by a device group is the direct sum of the storage consumed by all devices currently assigned to that group.
- **Example**: If Group A contains Device 1 (100 MB) and Device 2 (200 MB), Group A's space usage is **300 MB**.

### 3. Team Space Usage & Managing Team Rollup

Teams govern access control and permissions over groups and packs. Because devices can be members of multiple groups, and teams can be organized into management hierarchies, team space follows specific rules:

- **Root Teams Only**: Storage space is computed **only for teams that have Admin Write permission (`admin=write`) and do not have a managing team set** (referred to as "root" or self-managed teams).
- **Managing Teams Take the Storage Space**: If a team has a managing team configured (`managing_team_id` is set), the team's personal space usage is set to **0 B**. Instead, **its managing team takes all the storage space** of that managed team, transitively including any sub-managed teams down the chain.
- **Device Deduplication**: A single device can belong to multiple groups associated with the team or its managed child teams. The worker deduplicates devices by ID so that each unique physical endpoint is counted **only once**, preventing artificial inflation of storage numbers.
- **Display in the Console**: Teams that do not have `admin=write` or that are managed by another team do not display storage usage in the console to avoid confusion.

### 4. User Quota Computation

User storage usage represents the personal storage quota consumed by a user's managed resources:

- **Root Team Membership Required**: Quota is counted **only if** the team does not have a managing team. That is, a user must be a member of at least one qualifying root team (`admin=write` and `managing_team_id is None`).
- **Managed Teams Incur No Quota**: If a user is only a member of managed teams (teams that have a parent managing team set), the user's storage quota is **0 B**.
- **Aggregated & Deduplicated Devices**: For users in qualifying root teams, the worker identifies all devices across all device groups linked to those root teams and any child teams they manage. All device IDs are deduplicated across all teams and groups before summing their storage.
- **Users in No Teams**: Users who do not belong to any qualifying root team have a storage usage of **0 B**.

---

## The Superuser & Delegated Admin Architectural Pattern

Organizations often need to delegate administrative privileges to team leads or departmental IT staff without granting them organizational billing or storage quotas.

The managing team storage model directly enables this pattern:

### Organizational Example

Suppose an organization has:

1. **Root Team ("Security Operations HQ")**:
   - `admin=write`
   - `managing_team_id = None` (Root team)
   - **Member**: Organization Superuser (`admin@company.com`)
2. **Departmental Team ("Engineering Admin")**:
   - `admin=write`
   - `managing_team_id = Security Operations HQ` (Managed by HQ)
   - **Member**: Engineering Lead (`eng-lead@company.com`)
   - **Linked Groups**: "Engineering Workstations" (50 devices totaling 10 GB)

### Outcome

- **Storage Rollup**: Because "Engineering Admin" is managed by "Security Operations HQ", the **managing team takes the storage space**. "Security Operations HQ" reflects the 10 GB of storage.
- **Superuser Quota**: The Superuser (`admin@company.com`), as a member of the root team, has the **10 GB counted towards their quota**.
- **Delegated Admin Quota**: The Engineering Lead (`eng-lead@company.com`) has **0 B counted towards their quota**. They retain full administrative rights over their department's workstations, but do not consume personal storage allocation.

This ensures that organizations can designate one root superuser account that bears the entire storage quota, while sub-administrators operate freely within their managed boundaries.

---

## Background Worker Automation

Storage metrics are computed asynchronously in the background so that querying pages remain instantaneous.

- **Execution Interval**: By default, the space usage background worker executes every **15 minutes**.
- **Sequential Recalculation**:
  1. Computes device group usages from individual devices.
  2. Computes qualifying root team usages by rolling up group devices across managing hierarchies.
  3. Computes user quotas across all qualifying root team memberships.

### Configuration Variables

You can configure the worker via environment variables:

| Environment Variable                    | Default                      | Description                                                             |
|:----------------------------------------|:-----------------------------|:------------------------------------------------------------------------|
| `RADEGAST_ENABLE_SPACE_USAGE_WORKER`    | `true`                       | Enables or disables the background space usage worker.                  |
| `RADEGAST_SPACE_USAGE_INTERVAL_MINUTES` | `15`                         | Interval in minutes between periodic space recalculations.              |
| `RADEGAST_WORKER_LOCK_PATH`             | `/tmp/radegast-console.lock` | Path to the shared file lock used for single-instance worker execution. |

---

## Monitoring Storage in the Web Console

Storage consumption is visible across several areas of the Radegast Web Console:

1. **Dashboard Overview**:
   - The **Devices by Group** card shows the formatted storage next to device counts.
   - The **Devices by Team** card displays storage for qualifying root teams.
2. **Device Groups Page (`/groups`)**:
   - The groups table includes a **Space Used** column.
   - The group details page displays a storage badge in the header and space used per device.
3. **Teams Page (`/teams`)**:
   - The teams table displays **Space Used** for root teams with admin write permissions, displaying a dash (`-`) for managed or non-admin teams.
   - The team details page displays a **Space used** header badge.
4. **User Settings (`/settings`)**:
   - The **Storage Quota & Account** card displays your personal quota and total storage used.
5. **Admin Panel (`/admin`)**:
   - **Users Tab**: Includes a sortable **Space Used** column and a summary metric of **Total user storage**.
   - **Stats Tab**: Includes a dedicated **User Storage & Quota Stats** section displaying top users by storage consumption with visual distribution bars.
