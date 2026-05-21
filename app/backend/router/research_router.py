import json

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from app.backend.schemas import AuthUser, ResearchRequest, ResearchResponse
from app.backend.service import WorkflowService, get_workflow_service
from app.backend.service.auth_service import get_current_user


router = APIRouter(prefix="/api/v1/research", tags=["research"])


@router.post("/run", response_model=ResearchResponse)
async def run_research(
    payload: ResearchRequest,
    current_user: AuthUser = Depends(get_current_user),
    workflow_service: WorkflowService = Depends(get_workflow_service),
) -> ResearchResponse:
    final = await workflow_service.run(
        query=payload.query,
        user_id=current_user.id,
        thread_id=payload.thread_id,
        tenant_id=current_user.tenant_id,
        max_iterations=payload.max_iterations,
        enable_memory=payload.enable_memory,
        use_local_kb=payload.use_local_kb,
    )
    return ResearchResponse(
        query=payload.query,
        user_id=current_user.id,
        thread_id=payload.thread_id,
        tenant_id=current_user.tenant_id,
        final=final,
    )


@router.post("/stream")
async def stream_research(
    payload: ResearchRequest,
    current_user: AuthUser = Depends(get_current_user),
    workflow_service: WorkflowService = Depends(get_workflow_service),
) -> StreamingResponse:
    async def event_stream():
        start_event = {"type": "status", "message": "任务已接收，正在初始化多智能体链路"}
        yield f"data: {json.dumps(start_event, ensure_ascii=False)}\n\n"
        async for event in workflow_service.stream_events(
            query=payload.query,
            user_id=current_user.id,
            thread_id=payload.thread_id,
            tenant_id=current_user.tenant_id,
            max_iterations=payload.max_iterations,
            enable_memory=payload.enable_memory,
            use_local_kb=payload.use_local_kb,
        ):
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")
