"""FastAPI backend for LLM Council."""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import List, Dict, Any
import uuid
import json
import asyncio

from . import storage
from .council import (
    build_metadata,
    calculate_aggregate_rankings,
    CONTINUE_MIN_SUCCESSFUL_RESPONSES,
    extract_user_constraints,
    generate_conversation_title,
    pending_member_statuses,
    run_full_council,
    stage1_collect_responses_detailed,
    stage2_collect_rankings,
    stage3_synthesize_final,
)
from .config import MIN_SUCCESSFUL_RESPONSES

app = FastAPI(title="LLM Council API")

# Enable CORS for local development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class CreateConversationRequest(BaseModel):
    """Request to create a new conversation."""
    pass


class SendMessageRequest(BaseModel):
    """Request to send a message in a conversation."""
    content: str


class ContinueCouncilRequest(BaseModel):
    """Request to continue after one or more council members failed."""
    content: str
    stage1: List[Dict[str, Any]]
    stage1_statuses: List[Dict[str, Any]] = []
    constraints: Dict[str, Any] = {}


class RetryChairmanRequest(BaseModel):
    """Retry only Stage 3 for an existing assistant message."""
    content: str
    stage1: List[Dict[str, Any]]
    stage2: List[Dict[str, Any]]
    metadata: Dict[str, Any] = {}


class RetryPeerEvaluationsRequest(BaseModel):
    """Retry Stage 2 using stored successful Stage 1 answers."""
    content: str
    stage1: List[Dict[str, Any]]
    stage2: List[Dict[str, Any]] = []
    metadata: Dict[str, Any] = {}
    retry_failed_only: bool = False


class ConversationMetadata(BaseModel):
    """Conversation metadata for list view."""
    id: str
    created_at: str
    title: str
    message_count: int


class Conversation(BaseModel):
    """Full conversation with all messages."""
    id: str
    created_at: str
    title: str
    messages: List[Dict[str, Any]]


@app.get("/")
async def root():
    """Health check endpoint."""
    return {"status": "ok", "service": "LLM Council API"}


@app.get("/api/conversations", response_model=List[ConversationMetadata])
async def list_conversations():
    """List all conversations (metadata only)."""
    return storage.list_conversations()


@app.post("/api/conversations", response_model=Conversation)
async def create_conversation(request: CreateConversationRequest):
    """Create a new conversation."""
    conversation_id = str(uuid.uuid4())
    conversation = storage.create_conversation(conversation_id)
    return conversation


@app.get("/api/conversations/{conversation_id}", response_model=Conversation)
async def get_conversation(conversation_id: str):
    """Get a specific conversation with all its messages."""
    conversation = storage.get_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conversation


@app.post("/api/conversations/{conversation_id}/message")
async def send_message(conversation_id: str, request: SendMessageRequest):
    """
    Send a message and run the 3-stage council process.
    Returns the complete response with all stages.
    """
    # Check if conversation exists
    conversation = storage.get_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    # Check if this is the first message
    is_first_message = len(conversation["messages"]) == 0

    # Add user message
    storage.add_user_message(conversation_id, request.content)

    # If this is the first message, generate a title
    if is_first_message:
        title = await generate_conversation_title(request.content)
        storage.update_conversation_title(conversation_id, title)

    # Run the 3-stage council process
    stage1_results, stage2_results, stage3_result, metadata = await run_full_council(
        request.content
    )

    # Add assistant message with all stages
    storage.add_assistant_message(
        conversation_id,
        stage1_results,
        stage2_results,
        stage3_result,
        metadata
    )

    # Return the complete response with metadata
    return {
        "stage1": stage1_results,
        "stage2": stage2_results,
        "stage3": stage3_result,
        "metadata": metadata
    }


async def stream_remaining_council_events(
    conversation_id: str,
    user_query: str,
    stage1_results: List[Dict[str, Any]],
    stage1_statuses: List[Dict[str, Any]],
    constraints: Dict[str, Any],
    user_override: bool = False,
):
    """Yield SSE payloads for Stage 2 and Stage 3, then persist the message."""
    yield f"data: {json.dumps({'type': 'stage2_start'})}\n\n"
    async def stage2_event_callback(event):
        stage2_events.append(event)

    stage2_events = []
    stage2_results, label_to_model, stage2_metadata = await stage2_collect_rankings(
        user_query,
        stage1_results,
        constraints=constraints,
        stage2_event_callback=stage2_event_callback,
    )
    aggregate_rankings = calculate_aggregate_rankings(stage2_results, label_to_model)
    metadata = build_metadata(
        label_to_model=label_to_model,
        aggregate_rankings=aggregate_rankings,
        stage1_statuses=stage1_statuses,
        constraints=constraints,
        user_override=user_override,
        requires_continue=False,
    )
    metadata.update(stage2_metadata)
    for event in stage2_events:
        event_type = event.get("type", "peer_event")
        yield f"data: {json.dumps({'type': event_type, **event})}\n\n"
    yield f"data: {json.dumps({'type': 'stage2_complete', 'data': stage2_results, 'metadata': metadata})}\n\n"

    if metadata.get("peer_stage_summary", {}).get("requires_user_choice"):
        completed_existing = storage.complete_pending_assistant_message(
            conversation_id,
            stage1_results,
            stage2_results,
            None,
            metadata,
        )
        if not completed_existing:
            storage.add_assistant_message(
                conversation_id,
                stage1_results,
                stage2_results,
                None,
                metadata,
            )
        yield f"data: {json.dumps({'type': 'peer_stage_blocked', 'data': metadata.get('peer_stage_summary')})}\n\n"
        yield f"data: {json.dumps({'type': 'complete'})}\n\n"
        return

    chairman_events = []

    async def chairman_event_callback(event):
        chairman_events.append(event)

    stage3_result = await stage3_synthesize_final(
        user_query,
        stage1_results,
        stage2_results,
        constraints=constraints,
        event_callback=chairman_event_callback,
    )
    metadata["stage1_summary"] = {
        "successful": len(stage1_results),
        "failed": max(len(stage1_statuses or []) - len(stage1_results), 0),
        "total": len(stage1_statuses or []),
        "minimum_required": MIN_SUCCESSFUL_RESPONSES,
        "continue_minimum_required": CONTINUE_MIN_SUCCESSFUL_RESPONSES,
        "blocked": False,
        "can_continue": True,
        "override_available": False,
        "requires_continue": False,
        "user_override": user_override,
        "continued_with": len(stage1_results) if user_override else None,
        "message": (
            f"The council was completed with {len(stage1_results)} members after user approval."
            if user_override else "All required council responses were collected."
        ),
    }
    metadata["user_override"] = user_override
    metadata["chairman_result"] = stage3_result

    for event in chairman_events:
        event_type = event.get("type", "chairman_event")
        yield f"data: {json.dumps({'type': event_type, **event})}\n\n"
    yield f"data: {json.dumps({'type': 'stage3_complete', 'data': stage3_result})}\n\n"

    completed_existing = storage.complete_pending_assistant_message(
        conversation_id,
        stage1_results,
        stage2_results,
        stage3_result,
        metadata
    )
    if not completed_existing:
        storage.add_assistant_message(
            conversation_id,
            stage1_results,
            stage2_results,
            stage3_result,
            metadata,
        )
    yield f"data: {json.dumps({'type': 'complete'})}\n\n"


@app.post("/api/conversations/{conversation_id}/message/stream")
async def send_message_stream(conversation_id: str, request: SendMessageRequest):
    """
    Send a message and stream the 3-stage council process.
    Returns Server-Sent Events as each stage completes.
    """
    # Check if conversation exists
    conversation = storage.get_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    # Check if this is the first message
    is_first_message = len(conversation["messages"]) == 0

    async def event_generator():
        try:
            # Add user message
            storage.add_user_message(conversation_id, request.content)

            # Start title generation in parallel (don't await yet)
            title_task = None
            if is_first_message:
                title_task = asyncio.create_task(generate_conversation_title(request.content))

            # Stage 1: Collect responses
            pending_statuses = pending_member_statuses()
            yield f"data: {json.dumps({'type': 'stage1_start', 'metadata': {'stage1_statuses': pending_statuses, 'minimum_required': MIN_SUCCESSFUL_RESPONSES, 'continue_minimum_required': CONTINUE_MIN_SUCCESSFUL_RESPONSES}})}\n\n"

            status_queue = asyncio.Queue()

            async def status_callback(status):
                await status_queue.put(status)

            stage1_task = asyncio.create_task(
                stage1_collect_responses_detailed(request.content, status_callback=status_callback)
            )

            while not stage1_task.done():
                try:
                    status = await asyncio.wait_for(status_queue.get(), timeout=0.2)
                    yield f"data: {json.dumps({'type': 'model_status', 'data': status})}\n\n"
                except asyncio.TimeoutError:
                    continue

            stage1_details = await stage1_task
            while not status_queue.empty():
                status = await status_queue.get()
                yield f"data: {json.dumps({'type': 'model_status', 'data': status})}\n\n"

            metadata = build_metadata(
                stage1_statuses=stage1_details["statuses"],
                stage1_summary=stage1_details["summary"],
                constraints=stage1_details["constraints"],
                requires_continue=stage1_details["summary"]["requires_continue"],
            )
            stage1_results = stage1_details["responses"]
            yield f"data: {json.dumps({'type': 'stage1_complete', 'data': stage1_results, 'metadata': metadata})}\n\n"

            if stage1_details["summary"]["blocked"]:
                storage.add_assistant_message(
                    conversation_id,
                    stage1_results,
                    [],
                    None,
                    metadata,
                )
                if title_task:
                    title = await title_task
                    storage.update_conversation_title(conversation_id, title)
                    yield f"data: {json.dumps({'type': 'title_complete', 'data': {'title': title}})}\n\n"
                if stage1_details["summary"]["requires_continue"]:
                    yield f"data: {json.dumps({'type': 'continue_required', 'message': stage1_details['summary']['message'], 'metadata': metadata})}\n\n"
                else:
                    yield f"data: {json.dumps({'type': 'council_blocked', 'message': stage1_details['summary']['message'], 'metadata': metadata})}\n\n"
                yield f"data: {json.dumps({'type': 'complete'})}\n\n"
                return

            async for event in stream_remaining_council_events(
                conversation_id,
                request.content,
                stage1_results,
                stage1_details["statuses"],
                stage1_details["constraints"],
            ):
                yield event

            # Wait for title generation if it was started
            if title_task:
                title = await title_task
                storage.update_conversation_title(conversation_id, title)
                yield f"data: {json.dumps({'type': 'title_complete', 'data': {'title': title}})}\n\n"

        except Exception as e:
            # Send error event
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        }
    )


@app.post("/api/conversations/{conversation_id}/message/continue/stream")
async def continue_message_stream(conversation_id: str, request: ContinueCouncilRequest):
    """
    Continue Stage 2 and Stage 3 after failed models have been acknowledged.
    """
    conversation = storage.get_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    if len(request.stage1) < CONTINUE_MIN_SUCCESSFUL_RESPONSES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"At least {CONTINUE_MIN_SUCCESSFUL_RESPONSES} successful responses are "
                "required before continuing."
            ),
        )

    async def event_generator():
        try:
            async for event in stream_remaining_council_events(
                conversation_id,
                request.content,
                request.stage1,
                request.stage1_statuses,
                request.constraints or extract_user_constraints(request.content),
                user_override=True,
            ):
                yield event
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        }
    )


@app.post("/api/conversations/{conversation_id}/chairman/retry/stream")
async def retry_chairman_stream(conversation_id: str, request: RetryChairmanRequest):
    """Retry only chairman synthesis using stored Stage 1 and Stage 2 data."""
    conversation = storage.get_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    async def event_generator():
        try:
            constraints = request.metadata.get("constraints") or extract_user_constraints(request.content)
            chairman_events = []

            async def chairman_event_callback(event):
                chairman_events.append(event)

            stage3_result = await stage3_synthesize_final(
                request.content,
                request.stage1,
                request.stage2,
                constraints=constraints,
                event_callback=chairman_event_callback,
            )
            metadata = {
                **(request.metadata or {}),
                "constraints": constraints,
                "chairman_result": stage3_result,
            }
            storage.update_latest_assistant_stage3(conversation_id, stage3_result, metadata)

            for event in chairman_events:
                event_type = event.get("type", "chairman_event")
                yield f"data: {json.dumps({'type': event_type, **event})}\n\n"
            yield f"data: {json.dumps({'type': 'stage3_complete', 'data': stage3_result, 'metadata': metadata})}\n\n"
            yield f"data: {json.dumps({'type': 'complete'})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        }
    )


@app.post("/api/conversations/{conversation_id}/peer/retry/stream")
async def retry_peer_evaluations_stream(
    conversation_id: str,
    request: RetryPeerEvaluationsRequest,
):
    """Retry Stage 2 only; do not regenerate Stage 1."""
    conversation = storage.get_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    async def event_generator():
        try:
            constraints = request.metadata.get("constraints") or extract_user_constraints(request.content)
            stage2_events = []

            async def stage2_event_callback(event):
                stage2_events.append(event)

            stage2_results, label_to_model, stage2_metadata = await stage2_collect_rankings(
                request.content,
                request.stage1,
                constraints=constraints,
                stage2_event_callback=stage2_event_callback,
            )
            metadata = {
                **(request.metadata or {}),
                **stage2_metadata,
                "label_to_model": label_to_model,
                "aggregate_rankings": calculate_aggregate_rankings(stage2_results, label_to_model),
                "constraints": constraints,
            }
            storage.complete_pending_assistant_message(
                conversation_id,
                request.stage1,
                stage2_results,
                None,
                metadata,
            ) or storage.add_assistant_message(
                conversation_id,
                request.stage1,
                stage2_results,
                None,
                metadata,
            )
            for event in stage2_events:
                event_type = event.get("type", "peer_event")
                yield f"data: {json.dumps({'type': event_type, **event})}\n\n"
            yield f"data: {json.dumps({'type': 'stage2_complete', 'data': stage2_results, 'metadata': metadata})}\n\n"
            yield f"data: {json.dumps({'type': 'complete'})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'peer_stage_failed', 'message': str(e)})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        }
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
