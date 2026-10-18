"""固定共享 OpenSandbox 实例的 DeepAgents 后端适配。"""
from datetime import timedelta
from threading import RLock
from deepagents.backends.sandbox import BaseSandbox
from deepagents.backends.protocol import ExecuteResponse, FileUploadResponse, FileDownloadResponse
from opensandbox.config.connection_sync import ConnectionConfigSync
from opensandbox.models.execd import RunCommandOpts
from opensandbox.models.filesystem import WriteEntry
from opensandbox.sync import SandboxSync
from app.config import get_settings

class SharedSandboxBackend(BaseSandbox):
    """将 DeepAgents 文件原语路由到配置的单个共享沙箱，禁止宿主回退。"""
    def __init__(self):
        """保存固定实例配置，首次实际 I/O 时才建立连接。"""
        settings = get_settings()
        if not settings.open_sandbox_id or not settings.open_sandbox_base_url:
            raise RuntimeError("OpenSandbox 配置缺失")
        self._sandbox_id = str(settings.open_sandbox_id)
        self._connection = ConnectionConfigSync(
            api_key=settings.open_sandbox_api_key,
            domain=settings.open_sandbox_base_url.replace('http://', '').replace('https://', ''),
            protocol='https' if settings.open_sandbox_base_url.startswith('https') else 'http',
            request_timeout=timedelta(seconds=settings.open_sandbox_connect_timeout_seconds),
        )
        self._connect_timeout = timedelta(seconds=settings.open_sandbox_connect_timeout_seconds)
        self._sandbox: SandboxSync | None = None
        self._lock = RLock()
        self._timeout = settings.open_sandbox_command_timeout

    def _client(self) -> SandboxSync:
        """连接固定共享实例，失败向调用工具返回沙箱错误而不回退宿主。"""
        with self._lock:
            if self._sandbox is None:
                self._sandbox = SandboxSync.connect(
                    self._sandbox_id, connection_config=self._connection,
                    connect_timeout=self._connect_timeout,
                )
            return self._sandbox
    @property
    def id(self) -> str:
        """返回共享实例标识。"""
        return self._sandbox_id
    def close(self) -> None:
        """关闭客户端连接而不销毁共享实例。"""
        with self._lock:
            if self._sandbox is not None:
                self._sandbox.close()
                self._sandbox = None
    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        """在共享沙箱执行已由 Agent interrupt 审批的命令。"""
        try:
            result = self._client().commands.run(command, opts=RunCommandOpts(timeout=timedelta(seconds=timeout or self._timeout)))
            return ExecuteResponse(output=str(result), exit_code=getattr(result, 'exit_code', None))
        except Exception as exc:
            return ExecuteResponse(output=f"OpenSandbox execution failed: {type(exc).__name__}", exit_code=1)
    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        """上传文件到共享工作目录。"""
        result = []
        for path, data in files:
            try:
                self._client().files.write_files([WriteEntry(path=path, data=data, mode=644)]); result.append(FileUploadResponse(path=path))
            except Exception as exc:
                result.append(FileUploadResponse(path=path, error=type(exc).__name__))
        return result
    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        """从共享沙箱下载文件。"""
        result = []
        for path in paths:
            try: result.append(FileDownloadResponse(path=path, content=self._client().files.read_bytes(path)))
            except Exception as exc: result.append(FileDownloadResponse(path=path, error=type(exc).__name__))
        return result
