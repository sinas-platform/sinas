"""Request logs API endpoints for querying access logs."""
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from typing import List, Optional
from datetime import datetime

from app.core.auth import get_current_user_with_permissions, set_permission_used
from app.services.clickhouse_logger import clickhouse_logger
from app.schemas.request_log import (
    RequestLogResponse,
    RequestLogQueryParams,
    RequestLogStatsResponse
)

router = APIRouter(prefix="/request-logs", tags=["request-logs"])


@router.get("", response_model=List[RequestLogResponse])
async def list_request_logs(
    request: Request,
    user_id: Optional[str] = Query(None),
    start_time: Optional[datetime] = Query(None),
    end_time: Optional[datetime] = Query(None),
    permission: Optional[str] = Query(None),
    path_pattern: Optional[str] = Query(None),
    status_code: Optional[int] = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    current_user_data = Depends(get_current_user_with_permissions)
):
    """
    Query request logs with filters.

    Permissions:
    - Admins with "sinas.logs.read:all" can see all logs
    - Users can only see their own logs
    """
    current_user_id, permissions = current_user_data

    # Check if user has admin permission to see all logs
    can_see_all = permissions.get("sinas.logs.read:all", False)

    # If not admin, restrict to own logs only
    if not can_see_all:
        set_permission_used(request, "sinas.logs.read:own")
        if user_id and user_id != current_user_id:
            raise HTTPException(
                status_code=403,
                detail="You can only view your own request logs"
            )
        user_id = current_user_id
    else:
        set_permission_used(request, "sinas.logs.read:all")

    # Query ClickHouse
    logs = await clickhouse_logger.query_logs(
        user_id=user_id,
        start_time=start_time,
        end_time=end_time,
        permission=permission,
        path_pattern=path_pattern,
        status_code=status_code,
        limit=limit,
        offset=offset
    )

    # Convert to response models
    return [
        RequestLogResponse(
            request_id=str(log[0]),  # Convert UUID to string
            timestamp=log[1],
            user_id=log[2],
            user_email=log[3],
            permission_used=log[4],
            has_permission=log[5],
            method=log[6],
            path=log[7],
            query_params=log[8],
            request_body=log[9],
            user_agent=log[10],
            referer=log[11],
            ip_address=log[12],
            status_code=log[13],
            response_time_ms=log[14],
            response_size_bytes=log[15],
            resource_type=log[16],
            resource_id=log[17],
            group_id=log[18],
            error_message=log[19],
            error_type=log[20],
            metadata=log[21]
        )
        for log in logs
    ]


@router.get("/stats", response_model=RequestLogStatsResponse)
async def get_request_log_stats(
    request: Request,
    user_id: Optional[str] = Query(None),
    start_time: Optional[datetime] = Query(None),
    end_time: Optional[datetime] = Query(None),
    current_user_data = Depends(get_current_user_with_permissions)
):
    """
    Get aggregated statistics for request logs.

    Permissions:
    - Admins with "sinas.logs.read:all" can see stats for all users
    - Users can only see their own stats
    """
    current_user_id, permissions = current_user_data

    # Check if user has admin permission
    can_see_all = permissions.get("sinas.logs.read:all", False)

    # If not admin, restrict to own logs only
    if not can_see_all:
        set_permission_used(request, "sinas.logs.read:own")
        if user_id and user_id != current_user_id:
            raise HTTPException(
                status_code=403,
                detail="You can only view your own statistics"
            )
        user_id = current_user_id
    else:
        set_permission_used(request, "sinas.logs.read:all")

    # Build WHERE conditions
    conditions = []
    if user_id:
        conditions.append(f"user_id = '{user_id}'")
    if start_time:
        conditions.append(f"timestamp >= '{start_time.isoformat()}'")
    if end_time:
        conditions.append(f"timestamp <= '{end_time.isoformat()}'")

    where_clause = " AND ".join(conditions) if conditions else "1=1"

    # Query ClickHouse for stats
    if not clickhouse_logger.client:
        raise HTTPException(status_code=503, detail="ClickHouse not available")

    try:
        # Total requests and unique users
        stats_query = f"""
            SELECT
                count(*) as total_requests,
                uniq(user_id) as unique_users,
                avg(response_time_ms) as avg_response_time,
                countIf(status_code >= 400) / count(*) as error_rate
            FROM request_logs
            WHERE {where_clause}
        """
        stats_result = clickhouse_logger.client.query(stats_query)
        stats_row = stats_result.result_rows[0]

        # Top paths
        top_paths_query = f"""
            SELECT path, count(*) as cnt
            FROM request_logs
            WHERE {where_clause}
            GROUP BY path
            ORDER BY cnt DESC
            LIMIT 10
        """
        top_paths_result = clickhouse_logger.client.query(top_paths_query)

        # Top permissions
        top_perms_query = f"""
            SELECT permission_used, count(*) as cnt
            FROM request_logs
            WHERE {where_clause} AND permission_used != ''
            GROUP BY permission_used
            ORDER BY cnt DESC
            LIMIT 10
        """
        top_perms_result = clickhouse_logger.client.query(top_perms_query)

        return RequestLogStatsResponse(
            total_requests=stats_row[0],
            unique_users=stats_row[1],
            avg_response_time_ms=float(stats_row[2]) if stats_row[2] else 0.0,
            error_rate=float(stats_row[3]) if stats_row[3] else 0.0,
            top_paths=[{"path": row[0], "count": row[1]} for row in top_paths_result.result_rows],
            top_permissions=[{"permission": row[0], "count": row[1]} for row in top_perms_result.result_rows]
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch statistics: {str(e)}")
