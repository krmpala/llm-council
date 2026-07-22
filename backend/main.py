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
    generate_conversation_title,
    run_full_council,
    stage1_collect_responses_detailed,
    stage2_collect_rankings,
    stage3_synthesize_final,
)
from .config import COUNCIL_MODELS, MIN_SUCCESSFUL_RESPONSES

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
):
    """Yield SSE payloads for Stage 2 and Stage 3, then persist the message."""
    yield f"data: {json.dumps({'type': 'stage2_start'})}\n\n"
    stage2_results, label_to_model = await stage2_collect_rankings(user_query, stage1_results)
    aggregate_rankings = calculate_aggregate_rankings(stage2_results, label_to_model)
    metadata = build_metadata(
        label_to_model=label_to_model,
        aggregate_rankings=aggregate_rankings,
        stage1_statuses=stage1_statuses,
        stage1_summary={
            "successful": len(stage1_results),
            "failed": max(len(COUNCIL_MODELS) - len(stage1_results), 0),
            "total": len(COUNCIL_MODELS),
            "minimum_required": MIN_SUCCESSFUL_RESPONSES,
            "blocked": False,
            "can_continue": True,
            "requires_continue": False,
            "message": "The council continued with the available successful members.",
        },
        requires_continue=False,
    )
    yield f"data: {json.dumps({'type': 'stage2_complete', 'data': stage2_results, 'metadata': metadata})}\n\n"

    yield f"data: {json.dumps({'type': 'stage3_start'})}\n\n"
    stage3_result = await stage3_synthesize_final(user_query, stage1_results, stage2_results)
    yield f"data: {json.dumps({'type': 'stage3_complete', 'data': stage3_result})}\n\n"

    storage.add_assistant_message(
        conversation_id,
        stage1_results,
        stage2_results,
        stage3_result,
        metadata
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
            pending_statuses = [
                {
                    "model": model,
                    "provider": model.split("/", 1)[0],
                    "model_name": model.split("/", 1)[1] if "/" in model else model,
                    "status": "pending",
                    "attempts": 0,
                    "response": None,
                    "error": None,
                }
                for model in COUNCIL_MODELS
            ]
            yield f"data: {json.dumps({'type': 'stage1_start', 'metadata': {'stage1_statuses': pending_statuses, 'minimum_required': MIN_SUCCESSFUL_RESPONSES}})}\n\n"

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
                requires_continue=stage1_details["summary"]["requires_continue"],
            )
            stage1_results = stage1_details["responses"]
            yield f"data: {json.dumps({'type': 'stage1_complete', 'data': stage1_results, 'metadata': metadata})}\n\n"

            if stage1_details["summary"]["blocked"]:
                stage3_result = {
                    "model": "error",
                    "provider": "system",
                    "model_name": "minimum-participant-check",
                    "response": stage1_details["summary"]["message"],
                }
                yield f"data: {json.dumps({'type': 'council_blocked', 'message': stage1_details['summary']['message'], 'metadata': metadata})}\n\n"
                storage.add_assistant_message(
                    conversation_id,
                    stage1_results,
                    [],
                    stage3_result,
                    metadata,
                )
                if title_task:
                    title = await title_task
                    storage.update_conversation_title(conversation_id, title)
                    yield f"data: {json.dumps({'type': 'title_complete', 'data': {'title': title}})}\n\n"
                yield f"data: {json.dumps({'type': 'complete'})}\n\n"
                return

            if stage1_details["summary"]["requires_continue"]:
                if title_task:
                    title = await title_task
                    storage.update_conversation_title(conversation_id, title)
                    yield f"data: {json.dumps({'type': 'title_complete', 'data': {'title': title}})}\n\n"
                yield f"data: {json.dumps({'type': 'continue_required', 'message': stage1_details['summary']['message'], 'metadata': metadata})}\n\n"
                return

            async for event in stream_remaining_council_events(
                conversation_id,
                request.content,
                stage1_results,
                stage1_details["statuses"],
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

    if len(request.stage1) < MIN_SUCCESSFUL_RESPONSES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"At least {MIN_SUCCESSFUL_RESPONSES} successful responses are "
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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
