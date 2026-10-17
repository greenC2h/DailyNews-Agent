"""认证和用户设置 API。"""
from datetime import datetime, timedelta, timezone
import secrets
from fastapi import APIRouter, Cookie, Depends, HTTPException, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.config import get_settings
from app.db.database import get_db
from app.db.models import SessionToken, User
from app.services.security import hash_password, token_digest, verify_password
router = APIRouter(prefix="/api/v1", tags=["auth"]); settings = get_settings()
class RegisterRequest(BaseModel):
    """注册请求。"""
    email: EmailStr; password: str = Field(min_length=8, max_length=128); nickname: str = Field(default="", max_length=80)
class LoginRequest(BaseModel):
    """登录请求。"""
    email: EmailStr; password: str
class SettingsPatch(BaseModel):
    """设置字段级更新请求。"""
    nickname: str | None = None; timezone: str | None = None; topics: list[str] | None = None; keywords: list[str] | None = None; language: str | None = None; style: dict | None = None; daily_push_time: str | None = None; version: int | None = None
def current_user(session: str | None = Cookie(default=None), db: Session = Depends(get_db)) -> User:
    """解析当前用户并校验会话。"""
    if not session: raise HTTPException(401, detail={"code":"AUTH_REQUIRED","message":"请先登录"})
    record = db.get(SessionToken, token_digest(session))
    expires_at = record.expires_at.replace(tzinfo=timezone.utc) if record and record.expires_at.tzinfo is None else (record.expires_at if record else None)
    if not record or expires_at <= datetime.now(timezone.utc): raise HTTPException(401, detail={"code":"AUTH_REQUIRED","message":"登录已失效"})
    return record.user
def user_payload(user: User) -> dict:
    """转换为不含敏感字段的响应。"""
    return {"id":user.id,"email":user.email,"nickname":user.nickname,"timezone":user.timezone,"topics":user.topics,"keywords":user.keywords,"language":user.language,"style":user.style,"daily_push_time":user.daily_push_time,"settings_version":user.settings_version}
@router.post("/auth/register")
def register(payload: RegisterRequest, response: Response, db: Session = Depends(get_db)):
    """创建账号并建立会话。"""
    if db.scalar(select(User).where(User.email == payload.email.lower())): raise HTTPException(409, detail={"code":"EMAIL_EXISTS","message":"邮箱已注册"})
    user = User(email=payload.email.lower(), password_hash=hash_password(payload.password), nickname=payload.nickname); db.add(user); db.flush(); token = secrets.token_urlsafe(32); db.add(SessionToken(token_hash=token_digest(token), user_id=user.id, expires_at=datetime.now(timezone.utc)+timedelta(days=30))); db.commit(); response.set_cookie("session", token, httponly=True, samesite="lax", secure=settings.cookie_secure, max_age=2592000); return user_payload(user)
@router.post("/auth/login")
def login(payload: LoginRequest, response: Response, db: Session = Depends(get_db)):
    """校验密码并建立会话。"""
    user = db.scalar(select(User).where(User.email == payload.email.lower()))
    if not user or not verify_password(payload.password, user.password_hash): raise HTTPException(401, detail={"code":"INVALID_CREDENTIALS","message":"邮箱或密码错误"})
    token = secrets.token_urlsafe(32); db.add(SessionToken(token_hash=token_digest(token), user_id=user.id, expires_at=datetime.now(timezone.utc)+timedelta(days=30))); db.commit(); response.set_cookie("session", token, httponly=True, samesite="lax", secure=settings.cookie_secure, max_age=2592000); return user_payload(user)
@router.post("/auth/logout")
def logout(response: Response, session: str | None = Cookie(default=None), db: Session = Depends(get_db)):
    """注销会话。"""
    if session:
        record = db.get(SessionToken, token_digest(session))
        if record: db.delete(record); db.commit()
    response.delete_cookie("session"); return {"ok":True}
@router.get("/me")
def me(user: User = Depends(current_user)):
    """返回当前用户设置。"""
    return user_payload(user)
@router.patch("/settings")
def patch_settings(payload: SettingsPatch, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """更新设置并执行版本保护。"""
    if payload.version is not None and payload.version != user.settings_version: raise HTTPException(409, detail={"code":"SETTINGS_VERSION_CONFLICT","message":"设置已被更新，请刷新后重试"})
    for field in ("nickname","timezone","topics","keywords","language","style","daily_push_time"):
        value = getattr(payload, field)
        if value is not None: setattr(user, field, value)
    user.settings_version += 1; db.commit(); db.refresh(user); return user_payload(user)
