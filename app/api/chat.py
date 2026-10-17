"""主 Agent 对话接口，提供短消息 SSE 流。"""
import json
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from app.api.auth import current_user
from app.db.models import User
from app.services.chat import stream_reply
from app.db.database import SessionLocal
from app.db.news_models import ConversationMessage
from uuid import uuid4
router = APIRouter(prefix="/api/v1/chat", tags=["chat"])
class ChatRequest(BaseModel):
    """对话请求。"""
    message: str = Field(min_length=1, max_length=4000)
    thread_id: str | None = Field(default=None, max_length=100)
@router.post("/stream")
def chat_stream(payload: ChatRequest, user: User = Depends(current_user)):
    """以 SSE 返回主 Agent 的增量文本和任务提示。"""
    thread_id = payload.thread_id or str(uuid4())
    db = SessionLocal(); db.add(ConversationMessage(user_id=user.id, thread_id=thread_id, role="user", content=payload.message)); db.commit(); db.close()
    try: iterator = stream_reply(payload.message, user)
    except RuntimeError as exc: raise HTTPException(503, detail={"code":"MODEL_UNAVAILABLE","message":"对话模型暂时不可用"}) from exc
    def events():
        """将模型事件编码为 SSE。"""
        answer = []
        for item in iterator:
            answer.append(item.get("text", "")); item["thread_id"] = thread_id
            yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
        db = SessionLocal(); db.add(ConversationMessage(user_id=user.id, thread_id=thread_id, role="assistant", content="".join(answer))); db.commit(); db.close()
        yield "data: [DONE]\n\n"
    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})

@router.get("/{thread_id}")
def conversation(thread_id: str, user: User = Depends(current_user)):
    """返回当前用户线程最近 20 条消息。"""
    with SessionLocal() as db:
        rows = db.query(ConversationMessage).filter(ConversationMessage.user_id == user.id, ConversationMessage.thread_id == thread_id).order_by(ConversationMessage.created_at.desc()).limit(20).all()
        return {"thread_id":thread_id,"messages":[{"role":row.role,"content":row.content,"created_at":row.created_at} for row in reversed(rows)]}
