from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.team import Team
from app.models.associations import team_users

async def get_user_team_ids_transitive(user_id: int, db: AsyncSession) -> set[int]:
    """Get all team IDs where the user is a direct or virtual member (via managing teams)."""
    # 1. Get direct team IDs
    res = await db.execute(
        select(team_users.c.team_id).where(team_users.c.user_id == user_id)
    )
    direct_ids = {row[0] for row in res.all()}
    
    # 2. Expand transitively down the managing chain
    visited = set(direct_ids)
    queue = list(direct_ids)
    
    while queue:
        next_queue = []
        for team_id in queue:
            # Find all teams managed by team_id
            res_sub = await db.execute(
                select(Team.id).where(Team.managing_team_id == team_id)
            )
            for subteam_id in res_sub.scalars().all():
                if subteam_id not in visited:
                    visited.add(subteam_id)
                    next_queue.append(subteam_id)
        queue = next_queue
        
    return visited


async def is_user_member_of_team_transitive(team_id: int, user_id: int, db: AsyncSession) -> bool:
    """Check if the user is a member of the team directly or via managing team chain."""
    user_team_ids = await get_user_team_ids_transitive(user_id, db)
    return team_id in user_team_ids


async def has_team_admin_permission(team_id: int, user_id: int, db: AsyncSession) -> bool:
    """
    Check if user has admin permission on team_id.
    This checks if the team itself has admin=write and the user is a member,
    or transitively if any managing team in the chain has admin=write and the user is a member.
    """
    user_team_ids = await get_user_team_ids_transitive(user_id, db)
    
    # Walk up the managing chain of team_id
    curr_id = team_id
    visited = set()
    while curr_id is not None and curr_id not in visited:
        visited.add(curr_id)
        
        # Load team details for this node in the chain
        res = await db.execute(select(Team).where(Team.id == curr_id))
        team = res.scalar_one_or_none()
        if not team:
            break
            
        # If this team in the chain has admin permission, and the user is a member (direct or virtual) of this team:
        if team.permission_admin is not None and curr_id in user_team_ids:
            return True
            
        curr_id = team.managing_team_id
        
    return False


async def get_team_members_transitive(team_id: int, db: AsyncSession) -> set[int]:
    """Get all users who are direct or virtual members of a team (i.e. members of it or its managing teams)."""
    # Find all teams in the managing chain (team_id, and all teams that manage it)
    chain_ids = set()
    curr_id = team_id
    visited = set()
    while curr_id is not None and curr_id not in visited:
        visited.add(curr_id)
        chain_ids.add(curr_id)
        res = await db.execute(select(Team.managing_team_id).where(Team.id == curr_id))
        curr_id = res.scalar()
        
    if not chain_ids:
        return set()
        
    # Get all users of any team in the chain
    res_users = await db.execute(
        select(team_users.c.user_id).where(team_users.c.team_id.in_(list(chain_ids)))
    )
    return {row[0] for row in res_users.all()}
