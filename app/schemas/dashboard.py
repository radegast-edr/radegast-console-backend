from pydantic import BaseModel

from app.schemas.device import DeviceResponse
from app.schemas.log import LogResponse
from app.schemas.team import DeviceGroupResponse, TeamResponse


class DashboardResponse(BaseModel):
    teams: list[TeamResponse]
    groups: list[DeviceGroupResponse]
    devices: list[DeviceResponse]
    logs: list[LogResponse]
    team_device_counts: dict[int, int]
    group_device_counts: dict[int, int]
    device_groups_map: dict[int, list[str]]
    device_teams_map: dict[int, list[str]]
