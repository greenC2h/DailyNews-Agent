"""密码、会话和用户设置的校验及持久化服务。"""

import hashlib
import hmac
import secrets
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.db.models import User


class BriefingStylePatch(BaseModel):
    """校验用户提交的风格字段，允许仅修改其中一项。"""

    model_config = ConfigDict(extra="forbid")
    max_events: int | None = Field(default=None, ge=1, le=100, strict=True)
    summary_max_chars: int | None = Field(default=None, ge=80, le=10000, strict=True)
    summary_min_chars: Literal[80] | None = None
    layout: Literal["list", "grouped"] | None = None
    sort: str | None = Field(default=None, min_length=1, max_length=80)
    verbosity: str | None = Field(default=None, min_length=1, max_length=500)

    @model_validator(mode="after")
    def reject_null_fields(self) -> "BriefingStylePatch":
        """拒绝显式清空风格字段，避免持久化无效风格。"""
        if any(getattr(self, name) is None for name in self.model_fields_set):
            raise ValueError("风格字段不能为 null")
        return self


class SettingsPatch(BaseModel):
    """校验字段级偏好修改，只有每日时间允许显式清空。"""

    model_config = ConfigDict(extra="forbid")
    nickname: str | None = Field(default=None, max_length=80)
    timezone: str | None = Field(default=None, min_length=1, max_length=64)
    topics: list[Annotated[str, Field(min_length=1, max_length=200)]] | None = Field(default=None, max_length=50)
    keywords: list[Annotated[str, Field(min_length=1, max_length=200)]] | None = Field(default=None, max_length=100)
    language: Literal["zh-CN", "en"] | None = None
    style: BriefingStylePatch | None = None
    daily_push_time: str | None = Field(default=None, pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    version: int | None = Field(default=None, ge=1, strict=True)

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str | None) -> str | None:
        """确保时区可由系统 IANA 数据库解析。"""
        if value is not None:
            try:
                ZoneInfo(value)
            except (ZoneInfoNotFoundError, ValueError) as exc:
                raise ValueError("时区必须为有效 IANA 名称") from exc
        return value

    @field_validator("topics", "keywords")
    @classmethod
    def trim_items(cls, value: list[str] | None) -> list[str] | None:
        """清理条目前后空白并拒绝没有实际内容的主题和关键词。"""
        if value is None:
            return None
        items = [item.strip() for item in value]
        if any(not item for item in items):
            raise ValueError("主题和关键词不能为空白")
        return items

    @model_validator(mode="after")
    def reject_null_fields(self) -> "SettingsPatch":
        """只允许显式将每日推送时间设为 null。"""
        for name in self.model_fields_set - {"daily_push_time"}:
            if getattr(self, name) is None:
                raise ValueError(f"{name} 不能为 null")
        return self


class SettingsVersionConflict(Exception):
    """表示其他请求已抢先修改同一用户的设置。"""


def update_user_settings(db: Session, user: User, payload: SettingsPatch) -> User:
    """原子修改用户设置并保留未提交字段。

    Args:
        db: 调用方持有的数据库会话。
        user: 当前认证用户及其读取时的设置版本。
        payload: 已校验的字段级修改。

    Returns:
        提交并刷新后的用户对象。

    Raises:
        SettingsVersionConflict: 数据库版本已改变，事务已回滚。
    """
    expected_version = payload.version if payload.version is not None else user.settings_version
    changes = payload.model_dump(exclude_unset=True, exclude={"version"})
    if "style" in changes:
        changes["style"] = {**user.style, **changes["style"]}
    # 版本条件必须参与 UPDATE，单独读取后比较无法防止并发覆盖。
    statement = update(User).where(User.id == user.id, User.settings_version == expected_version)
    result = db.execute(
        statement.values(**changes, settings_version=User.settings_version + 1),
        execution_options={"synchronize_session": False},
    )
    if result.rowcount != 1:
        db.rollback()
        raise SettingsVersionConflict()
    # 重新加载本事务更新值，默认任务与设置必须一并提交或回滚。
    db.refresh(user)
    if {"daily_push_time", "timezone"} & changes.keys():
        from app.services.scheduler import sync_default_schedule

        sync_default_schedule(db, user)
    db.commit()
    db.refresh(user)
    return user


def hash_password(password: str) -> str:
    """使用随机盐生成 scrypt 密码哈希，返回可持久化的编码。"""
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    """以常量时间比较密码哈希，无效存储编码返回 False。"""
    try:
        algorithm, salt, digest = encoded.split("$", 2)
        if algorithm != "scrypt" or len(salt) != 32 or len(digest) != 128:
            return False
        actual = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=1)
        return hmac.compare_digest(actual, bytes.fromhex(digest))
    except (ValueError, TypeError, AttributeError):
        return False


def token_digest(token: str) -> str:
    """生成会话令牌摘要，原始令牌只保留在浏览器 Cookie 中。"""
    return hashlib.sha256(token.encode()).hexdigest()
