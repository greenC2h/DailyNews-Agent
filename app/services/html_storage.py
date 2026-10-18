"""简报 HTML 文件的受控存储、完整性校验与事务失败清理。"""
from dataclasses import dataclass
from hashlib import sha256
import logging
import os
from pathlib import Path
from uuid import uuid4

from sqlalchemy.orm import Session


logger = logging.getLogger("dailynews.html_storage")


class HtmlStorageError(OSError):
    """表示简报 HTML 文件不存在、路径不安全或完整性校验失败。"""


@dataclass(frozen=True)
class StoredHtml:
    """承载刚写入的 HTML 文件相对路径及不可变完整性元数据。"""

    relative_path: str
    absolute_path: Path
    size: int
    checksum: str


def _root_path(root: Path) -> Path:
    """解析并确保 HTML 根目录存在，返回绝对规范路径。"""
    resolved = Path(root).resolve()
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def _inside_root(path: Path, root: Path) -> bool:
    """判断路径解析后是否仍在指定 HTML 根目录内。"""
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def store_html(root: Path, user_id: str, briefing_id: str, content: str) -> StoredHtml:
    """原子写入新的简报 HTML，并返回相对路径、字节数和 SHA-256。

    Args:
        root: 应用配置的 HTML 根目录。
        user_id: 简报所属用户标识。
        briefing_id: 已生成的简报标识。
        content: 待保存的 UTF-8 HTML 文本。

    Returns:
        已写入文件的不可变元数据。

    Raises:
        OSError: 目录或文件无法安全写入时抛出。
    """
    base = _root_path(root)
    relative = Path(user_id) / f"{briefing_id}.html"
    target = (base / relative).resolve()
    if not _inside_root(target, base):
        raise HtmlStorageError("HTML 路径超出存储根目录")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
    data = content.encode("utf-8")
    try:
        with temporary.open("wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(target)
    except OSError:
        temporary.unlink(missing_ok=True)
        raise
    return StoredHtml(relative.as_posix(), target, len(data), sha256(data).hexdigest())


def apply_stored_html(briefing, stored: StoredHtml) -> None:
    """把新文件元数据写入尚未提交的简报 ORM 对象。

    Args:
        briefing: 即将提交的简报 ORM 对象。
        stored: 由 ``store_html`` 返回的文件元数据。
    """
    briefing.html_path = stored.relative_path
    briefing.html_size = stored.size
    briefing.html_sha256 = stored.checksum


def cleanup_uncommitted_html(path: Path, root: Path) -> None:
    """清理提交失败产生的文件；删除失败时隔离到不可服务的孤儿目录。

    Args:
        path: 本次事务刚写入、尚未成为数据库记录的目标文件。
        root: 应用配置的 HTML 根目录，用于限制可清理范围。

    Side Effects:
        成功时删除文件；无法删除时移动到 ``.orphaned`` 并记录错误日志，移动也
        失败时保留原文件但写入 critical 日志，供运维按路径核查。
    """
    base = _root_path(root)
    candidate = Path(path).resolve()
    if not _inside_root(candidate, base):
        logger.critical("refuse to clean HTML outside root path=%s", candidate)
        return
    try:
        candidate.unlink(missing_ok=True)
        logger.warning("removed uncommitted HTML path=%s", candidate)
        return
    except OSError as delete_error:
        quarantine = base / ".orphaned"
        destination = quarantine / f"{uuid4().hex}-{candidate.name}"
        try:
            quarantine.mkdir(parents=True, exist_ok=True)
            candidate.replace(destination)
        except OSError:
            logger.critical(
                "failed to clean or quarantine uncommitted HTML path=%s error=%s",
                candidate,
                type(delete_error).__name__,
                exc_info=True,
            )
            return
        logger.error(
            "quarantined uncommitted HTML path=%s quarantine=%s error=%s",
            candidate,
            destination,
            type(delete_error).__name__,
        )


def commit_stored_html(db: Session, briefing, stored: StoredHtml, root: Path) -> None:
    """提交已绑定 HTML 元数据的简报，失败时回滚并清理对应文件。

    Args:
        db: 当前业务数据库会话。
        briefing: 已通过 ``apply_stored_html`` 更新的简报对象。
        stored: 本事务写入的 HTML 文件元数据。
        root: 应用配置的 HTML 根目录。

    Raises:
        Exception: 保留原始数据库提交异常，文件清理不会掩盖该异常。
    """
    try:
        db.commit()
    except Exception:
        db.rollback()
        cleanup_uncommitted_html(stored.absolute_path, root)
        raise


def resolve_html_path(briefing, root: Path) -> Path:
    """解析简报文件路径，兼容历史绝对路径并限制新增相对路径范围。

    Args:
        briefing: 含 ``html_path`` 的简报 ORM 对象。
        root: 应用配置的 HTML 根目录。

    Returns:
        可供读取的绝对文件路径。

    Raises:
        HtmlStorageError: 路径为空、相对路径越界或文件不存在时抛出。
    """
    if not briefing.html_path:
        raise HtmlStorageError("HTML 路径为空")
    configured_root = _root_path(root)
    configured = Path(briefing.html_path)
    if configured.is_absolute():
        candidate = configured
    else:
        candidate = (configured_root / configured).resolve()
        if not _inside_root(candidate, configured_root):
            raise HtmlStorageError("HTML 相对路径超出存储根目录")
    if not candidate.is_file():
        raise HtmlStorageError("HTML 文件不存在")
    return candidate


def read_stored_html(briefing, root: Path) -> bytes:
    """读取简报 HTML，并校验新记录保存的大小与 SHA-256。

    旧记录的元数据为空时仅按历史路径读取，以保证绝对路径迁移兼容性。
    """
    path = resolve_html_path(briefing, root)
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise HtmlStorageError("HTML 文件无法读取") from exc
    expected_size = getattr(briefing, "html_size", None)
    expected_checksum = getattr(briefing, "html_sha256", None)
    if expected_size is not None and len(data) != expected_size:
        raise HtmlStorageError("HTML 文件大小不匹配")
    if expected_checksum and sha256(data).hexdigest() != expected_checksum:
        raise HtmlStorageError("HTML 文件校验和不匹配")
    return data
