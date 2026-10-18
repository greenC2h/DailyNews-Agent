"""HTML 存储相对路径、完整性与数据库失败清理回归测试。"""
from types import SimpleNamespace

import pytest

from app.services.html_storage import (
    HtmlStorageError,
    apply_stored_html,
    commit_stored_html,
    read_stored_html,
    store_html,
)


def test_new_html_uses_relative_path_and_integrity_metadata(tmp_path):
    """新文件只记录相对路径，并在读取时验证大小和校验和。"""
    stored = store_html(tmp_path / "html", "user-1", "brief-1", "<h1>完整性</h1>")
    briefing = SimpleNamespace(html_path="", html_size=None, html_sha256=None)
    apply_stored_html(briefing, stored)
    assert briefing.html_path == "user-1/brief-1.html"
    assert briefing.html_size == len("<h1>完整性</h1>".encode("utf-8"))
    assert len(briefing.html_sha256) == 64
    assert read_stored_html(briefing, tmp_path / "html") == "<h1>完整性</h1>".encode()


def test_legacy_absolute_path_remains_readable(tmp_path):
    """缺少新元数据的历史绝对路径简报继续可预览和下载。"""
    legacy = tmp_path / "legacy.html"
    legacy.write_text("<h1>历史简报</h1>", encoding="utf-8")
    briefing = SimpleNamespace(html_path=str(legacy), html_size=None, html_sha256=None)
    assert read_stored_html(briefing, tmp_path / "html") == legacy.read_bytes()


def test_integrity_mismatch_is_not_readable(tmp_path):
    """新记录被修改后，预览和邮件读取必须拒绝不匹配内容。"""
    stored = store_html(tmp_path / "html", "user-1", "brief-1", "<h1>原始</h1>")
    briefing = SimpleNamespace(html_path="", html_size=None, html_sha256=None)
    apply_stored_html(briefing, stored)
    stored.absolute_path.write_text("<h1>篡改</h1>", encoding="utf-8")
    with pytest.raises(HtmlStorageError, match="不匹配"):
        read_stored_html(briefing, tmp_path / "html")


def test_commit_failure_removes_new_html(tmp_path):
    """数据库提交失败后清理该事务新写入的文件，避免发布孤儿简报。"""
    stored = store_html(tmp_path / "html", "user-1", "brief-1", "<h1>未提交</h1>")

    class FailingSession:
        """模拟数据库最终提交失败并记录回滚调用。"""

        def __init__(self):
            """初始化回滚计数。"""
            self.rolled_back = False

        def commit(self):
            """模拟写入约束错误。"""
            raise RuntimeError("commit failed")

        def rollback(self):
            """记录调用方已恢复数据库事务。"""
            self.rolled_back = True

    session = FailingSession()
    with pytest.raises(RuntimeError, match="commit failed"):
        commit_stored_html(session, SimpleNamespace(), stored, tmp_path / "html")
    assert session.rolled_back
    assert not stored.absolute_path.exists()
