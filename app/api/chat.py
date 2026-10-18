"""持久化对话线程读取及中断恢复 API。"""
import json
from uuid import uuid4
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.api.auth import current_user
from app.db.database import get_db
from app.db.models import User
from app.db.news_models import ConversationMessage
from app.services.assistant_runtime import assistant_turn, pending_interrupts

router = APIRouter(prefix="/api/v1/chat", tags=["chat"])
class ChatRequest(BaseModel):
    """新消息请求。"""
    message: str = Field(min_length=1, max_length=4000)
    thread_id: str | None = Field(default=None, max_length=100)
class ResumeRequest(BaseModel):
    """补问或显式命令审批恢复请求。"""
    resume: object

def _stream(result: dict, thread_id: str):
    """将助手回复、审批中断和业务资源编码为 SSE。"""
    yield f"data: {json.dumps({'type':'message','text':result.get('text',''),'thread_id':thread_id,'resources':result.get('resources',[])},ensure_ascii=False)}\n\n"
    for interrupt in result.get('interrupts', []):
        yield f"data: {json.dumps({'type':'interrupt','thread_id':thread_id,**interrupt},ensure_ascii=False)}\n\n"
    yield "data: [DONE]\n\n"

def _save_message(user_id: str, thread_id: str, role: str, content: str) -> None:
    """将展示消息归档到用户私有关系数据；图状态由 Checkpointer 保存。"""
    from app.db.database import SessionLocal
    with SessionLocal() as db:
        db.add(ConversationMessage(user_id=user_id, thread_id=thread_id, role=role, content=content)); db.commit()

@router.post("/stream")
def chat_stream(payload: ChatRequest, user: User = Depends(current_user)):
    """执行持久助手并保存本轮消息或补问中断。"""
    thread_id = payload.thread_id or str(uuid4())
    result = assistant_turn(payload.message, user, thread_id)
    _save_message(user.id, thread_id, "user", payload.message)
    if result.get("text"):
        _save_message(user.id, thread_id, "assistant", result["text"])
    return StreamingResponse(_stream(result, thread_id), media_type="text/event-stream", headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})

@router.post("/{thread_id}/resume")
def resume_chat(thread_id: str, payload: ResumeRequest, user: User = Depends(current_user)):
    """恢复补问或 execute 审批并归档用户和助手消息。"""
    result = assistant_turn("", user, thread_id, payload.resume)
    _save_message(user.id, thread_id, "user", json.dumps(payload.resume, ensure_ascii=False))
    if result.get("text"):
        _save_message(user.id, thread_id, "assistant", result["text"])
    return StreamingResponse(_stream(result, thread_id), media_type="text/event-stream", headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})

@router.get("/{thread_id}")
def conversation(thread_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """返回用户私有会话归档及 PostgreSQL Checkpointer 中待处理审批。"""
    rows = db.scalars(select(ConversationMessage).where(ConversationMessage.user_id == user.id, ConversationMessage.thread_id == thread_id).order_by(ConversationMessage.created_at.desc()).limit(40)).all()
    interrupts = pending_interrupts(user, thread_id)
    return {"thread_id":thread_id,"messages":[{"role":row.role,"content":row.content,"created_at":row.created_at} for row in reversed(rows)],"interrupts":interrupts}
