"""主 Agent 对话接口，提供短消息 SSE 流。"""
import json
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from app.api.auth import current_user
from app.db.models import User
from app.services.chat import stream_reply
router = APIRouter(prefix="/api/v1/chat", tags=["chat"])
class ChatRequest(BaseModel):
    """对话请求。"""
    message: str = Field(min_length=1, max_length=4000)
    thread_id: str | None = Field(default=None, max_length=100)
@router.post("/stream")
def chat_stream(payload: ChatRequest, user: User = Depends(current_user)):
    """以 SSE 返回主 Agent 的增量文本和任务提示。"""
    try: iterator = stream_reply(payload.message, user)
    except RuntimeError as exc: raise HTTPException(503, detail={"code":"MODEL_UNAVAILABLE","message":"对话模型暂时不可用"}) from exc
    def events():
        """将模型事件编码为 SSE。"""
        for item in iterator: yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"
    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})
