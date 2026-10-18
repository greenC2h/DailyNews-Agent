"""认证和用户设置 API。"""

from datetime import datetime, timedelta, timezone
import logging
import secrets

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.database import get_db
from app.db.models import SessionToken, User
from app.db.news_models import ScheduledTask
from app.services.security import (
    SettingsPatch,
    SettingsVersionConflict,
    hash_password,
    token_digest,
    update_user_settings,
    verify_password,
)

router = APIRouter(prefix="/api/v1", tags=["auth"])
settings = get_settings()
logger = logging.getLogger(__name__)
SESSION_SECONDS = 7 * 24 * 60 * 60


class RegisterRequest(BaseModel):
    """注册邮箱、密码、昵称和浏览器检测到的可选时区。"""

    model_config = ConfigDict(extra="forbid")
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    nickname: str = Field(default="", max_length=80)
    timezone: str = Field(default="Asia/Shanghai", min_length=1, max_length=64)


class LoginRequest(BaseModel):
    """限制登录凭据长度，错误响应不区分账号是否存在。"""

    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class PasswordChangeRequest(BaseModel):
    """修改密码前验证旧密码，并校验新密码长度。"""

    model_config = ConfigDict(extra="forbid")
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


def current_user(session: str | None = Cookie(default=None), db: Session = Depends(get_db)) -> User:
    """解析当前会话并在访问时清理过期令牌。"""
    if not session:
        raise HTTPException(401, detail={"code": "AUTH_REQUIRED", "message": "请先登录"})
    record = db.get(SessionToken, token_digest(session))
    expires_at = None
    if record:
        expires_at = record.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
    if not record or expires_at <= datetime.now(timezone.utc):
        if record:
            logger.info("auth_session_expired", extra={"user_id": record.user_id})
            db.delete(record)
            db.commit()
        raise HTTPException(401, detail={"code": "AUTH_REQUIRED", "message": "登录已失效"})
    return record.user


def user_payload(user: User) -> dict:
    """返回用户设置及版本，不包含密码或会话摘要。"""
    return {
        "id": user.id, "email": user.email, "nickname": user.nickname,
        "timezone": user.timezone, "topics": user.topics, "keywords": user.keywords,
        "language": user.language, "style": user.style,
        "daily_push_time": user.daily_push_time, "settings_version": user.settings_version,
    }


def create_session(db: Session, user: User, response: Response) -> None:
    """持久化七天会话摘要并在响应中写入安全 Cookie。"""
    token = secrets.token_urlsafe(32)
    db.add(SessionToken(
        token_hash=token_digest(token), user_id=user.id,
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=SESSION_SECONDS),
    ))
    db.commit()
    response.set_cookie(
        "session", token, httponly=True, samesite="lax",
        secure=settings.cookie_secure, max_age=SESSION_SECONDS,
    )


@router.post("/auth/register")
def register(payload: RegisterRequest, response: Response, db: Session = Depends(get_db)):
    """规范化邮箱、创建用户并建立会话，唯一约束处理并发注册。"""
    try:
        validated = SettingsPatch(timezone=payload.timezone)
    except ValueError as exc:
        raise HTTPException(422, detail={"code": "INVALID_TIMEZONE", "message": "请选择有效时区"}) from exc
    email = payload.email.lower()
    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(409, detail={"code": "EMAIL_EXISTS", "message": "邮箱已注册"})
    user = User(email=email, password_hash=hash_password(payload.password), nickname=payload.nickname, timezone=validated.timezone)
    db.add(user)
    try:
        db.flush()
        create_session(db, user, response)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, detail={"code": "EMAIL_EXISTS", "message": "邮箱已注册"}) from exc
    logger.info("auth_registered", extra={"user_id": user.id})
    return user_payload(user)


@router.post("/auth/login")
def login(payload: LoginRequest, response: Response, db: Session = Depends(get_db)):
    """校验密码并建立会话，对不存在邮箱和错误密码使用相同错误。"""
    user = db.scalar(select(User).where(User.email == payload.email.lower()))
    if not user or not verify_password(payload.password, user.password_hash):
        logger.info("auth_login_rejected")
        raise HTTPException(401, detail={"code": "INVALID_CREDENTIALS", "message": "邮箱或密码错误"})
    create_session(db, user, response)
    logger.info("auth_login", extra={"user_id": user.id})
    return user_payload(user)


@router.post("/auth/logout")
def logout(response: Response, session: str | None = Cookie(default=None), db: Session = Depends(get_db)):
    """立即删除当前会话并清除浏览器 Cookie。"""
    if session:
        record = db.get(SessionToken, token_digest(session))
        if record:
            logger.info("auth_logout", extra={"user_id": record.user_id})
            db.delete(record)
            db.commit()
    response.delete_cookie("session")
    return {"ok": True}


@router.post("/auth/password")
def change_password(
    payload: PasswordChangeRequest,
    session: str | None = Cookie(default=None),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    """验证旧密码后原子更新哈希并撤销当前令牌之外的全部会话。"""
    if not verify_password(payload.current_password, user.password_hash):
        logger.info("auth_password_rejected", extra={"user_id": user.id})
        raise HTTPException(401, detail={"code": "INVALID_CREDENTIALS", "message": "当前密码错误"})
    # 旧哈希参与条件，阻止并发密码修改以过时凭据覆盖新密码。
    result = db.execute(
        update(User).where(User.id == user.id, User.password_hash == user.password_hash)
        .values(password_hash=hash_password(payload.new_password)),
        execution_options={"synchronize_session": False},
    )
    if result.rowcount != 1:
        db.rollback()
        raise HTTPException(409, detail={"code": "PASSWORD_CONFLICT", "message": "密码已更新，请重新登录"})
    db.execute(delete(SessionToken).where(SessionToken.user_id == user.id, SessionToken.token_hash != token_digest(session)))
    db.commit()
    logger.info("auth_password_changed", extra={"user_id": user.id})
    return {"ok": True}


@router.get("/auth/me")
@router.get("/me")
def me(user: User = Depends(current_user)):
    """返回当前用户设置，支持基线及现有页面两种恢复路径。"""
    return user_payload(user)


@router.patch("/settings")
def patch_settings(payload: SettingsPatch, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """通过共享设置服务执行字段级更新和原子版本保护。"""
    next_style = {**user.style, **(payload.style.model_dump(exclude_unset=True) if payload.style else {})}
    default_task = db.query(ScheduledTask).filter(ScheduledTask.user_id == user.id, ScheduledTask.is_default.is_(True)).first()
    if default_task and default_task.article_limit is not None and default_task.article_limit < next_style.get("max_events", 5):
        raise HTTPException(422, detail={"code": "ARTICLE_LIMIT_TOO_LOW", "message": "默认每日任务的原文采集上限低于事件数，请先调整采集上限"})
    try:
        return user_payload(update_user_settings(db, user, payload))
    except SettingsVersionConflict as exc:
        raise HTTPException(409, detail={"code": "SETTINGS_VERSION_CONFLICT", "message": "设置已被更新，请刷新后重试"}) from exc
