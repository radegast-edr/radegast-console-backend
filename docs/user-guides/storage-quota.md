# Storage Quota and Space Usage

## Overview

Radegast EDR tracks storage consumption hierarchically across devices, device groups, teams, and users. This lets organizations monitor telemetry volume, plan database capacity, and enforce multi-tenant quotas.

A background worker computes space usage automatically, maintaining console performance while keeping storage metrics accurate.

---

## Storage Calculation Model

Storage calculation uses a hierarchical rollup model with device deduplication:

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

Each device tracks the disk space consumed by its telemetry logs, alert events, and communication buffers. The device record stores this metric in `total_space_used`.

### 2. Device Group Space Usage

Device groups aggregate devices by workload, department, or operating system:

- **Calculation Rule**: Group space usage is the direct sum of disk space consumed by all assigned devices.
- **Example**: If Group A contains Device 1 (100 MB) and Device 2 (200 MB), Group A consumes **300 MB**.

### 3. Team Space Usage and Hierarchy Rollup

Teams govern access permissions for groups and detection packs. Because devices can belong to multiple groups, team storage follows these rules:

- **Root Teams Only**: The system computes storage space only for root teams (`admin=write` without a managing team).
- **Managing Team Allocation**: If a team has a parent managing team (`managing_team_id` is set), its personal space usage is **0 B**. The parent managing team inherits all storage consumed by child teams.
- **Endpoint Deduplication**: If a device belongs to multiple groups linked to a team, the worker counts each unique device ID only once.
- **Console Display**: Teams without administrative write permissions or teams managed by parent teams display a dash (`-`) for storage usage.

### 4. User Quota Computation

User storage reflects the quota consumed by resources managed by that user:

- **Root Team Membership**: Quota accrues only for members of qualifying root teams (`admin=write` and no managing team).
- **Managed Teams Incur No Quota**: Members of managed child teams incur **0 B** against their personal storage quota.
- **Deduplicated Summation**: The worker aggregates all endpoints linked to the user's root teams and deduplicates device IDs before summing storage.
- **Unassigned Users**: Users who belong to no qualifying root teams have a storage quota consumption of **0 B**.

---

## Superuser and Delegated Admin Architecture

Organizations frequently delegate administrative control over departments without allocating individual storage quotas.

The managing team storage model supports this architecture:

### Organizational Example

Consider an organization with this structure:

1. **Root Team ("Security Operations HQ")**:
   - `admin=write`
   - `managing_team_id = None` (Root team)
   - **Member**: Organization Superuser (`admin@company.com`)
2. **Child Team ("Engineering Admin")**:
   - `admin=write`
   - `managing_team_id = Security Operations HQ` (Managed team)
   - **Member**: Engineering Lead (`eng-lead@company.com`)
   - **Linked Group**: "Engineering Workstations" (50 endpoints totaling 10 GB)

### Storage Allocation Result

- **Team Rollup**: Because "Engineering Admin" is managed by "Security Operations HQ", the managing team inherits the 10 GB storage total.
- **Superuser Quota**: The superuser (`admin@company.com`) has 10 GB counted against their personal quota.
- **Delegated Admin Quota**: The engineering lead (`eng-lead@company.com`) has **0 B** counted against their quota, while retaining administrative control over departmental endpoints.

---

## Background Worker Automation

A background worker process recalculates storage metrics asynchronously:

- **Execution Interval**: The worker runs every 15 minutes by default.
- **Calculation Order**:
  1. Computes group usage from assigned devices.
  2. Rolls up qualifying root team totals across managing hierarchies.
  3. Computes user quotas from root team memberships.

### Configuration Variables

You can configure the worker with environment variables:

| Environment Variable | Default | Description |
|:---|:---|:---|
| `RADEGAST_ENABLE_SPACE_USAGE_WORKER` | `true` | Enables or disables the background space usage worker. |
| `RADEGAST_SPACE_USAGE_INTERVAL_MINUTES` | `15` | Interval in minutes between storage calculations. |
| `RADEGAST_WORKER_LOCK_PATH` | `/tmp/radegast-console.lock` | Path to the shared lock file for single-instance execution. |

---

## Monitor Storage in the Console

You can inspect storage consumption across multiple views:

1. **Dashboard Overview**:
   - The **Devices by Group** card displays formatted storage alongside device counts.
   - The **Devices by Team** card displays storage for qualifying root teams.
2. **Device Groups Page (`/groups`)**:
   - The groups table includes a **Space Used** column.
   - The group details page displays storage badges in the header and per device.
3. **Teams Page (`/teams`)**:
   - The teams table displays **Space Used** for qualifying root teams.
   - The team details page displays a **Space used** header badge.
4. **User Settings (`/settings`)**:
   - The **Storage Quota & Account** card displays your quota consumption and total allocated storage.
5. **Admin Panel (`/admin`)**:
   - **Users Tab**: Includes a sortable **Space Used** column and total platform storage metrics.
   - **Stats Tab**: Displays top users ranked by storage consumption.
