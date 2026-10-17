"""固定共享 OpenSandbox 实例的 DeepAgents 后端适配。"""
from datetime import timedelta
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
        """读取固定实例配置，不按用户创建或切换沙箱。"""
        settings = get_settings()
        if not settings.open_sandbox_id or not settings.open_sandbox_base_url:
            raise RuntimeError("OpenSandbox 配置缺失")
        self._sandbox = SandboxSync.connect(str(settings.open_sandbox_id), connection_config=ConnectionConfigSync(api_key=settings.open_sandbox_api_key, domain=settings.open_sandbox_base_url.replace('http://','').replace('https://',''), protocol='https' if settings.open_sandbox_base_url.startswith('https') else 'http'))
        self._timeout = settings.open_sandbox_command_timeout
    @property
    def id(self) -> str:
        """返回共享实例标识。"""
        return str(self._sandbox.id)
    def close(self) -> None:
        """关闭客户端连接而不销毁共享实例。"""
        self._sandbox.close()
    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        """在共享沙箱执行已由 Agent interrupt 审批的命令。"""
        try:
            result = self._sandbox.commands.run(command, opts=RunCommandOpts(timeout=timedelta(seconds=timeout or self._timeout)))
            return ExecuteResponse(output=str(result), exit_code=getattr(result, 'exit_code', None))
        except Exception as exc:
            return ExecuteResponse(output=f"OpenSandbox execution failed: {type(exc).__name__}", exit_code=1)
    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        """上传文件到共享工作目录。"""
        result = []
        for path, data in files:
            try:
                self._sandbox.files.write_files([WriteEntry(path=path, data=data, mode=644)]); result.append(FileUploadResponse(path=path))
            except Exception as exc:
                result.append(FileUploadResponse(path=path, error=type(exc).__name__))
        return result
    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        """从共享沙箱下载文件。"""
        result = []
        for path in paths:
            try: result.append(FileDownloadResponse(path=path, content=self._sandbox.files.read_bytes(path)))
            except Exception as exc: result.append(FileDownloadResponse(path=path, error=type(exc).__name__))
        return result
