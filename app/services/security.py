"""密码和会话安全辅助函数。"""
import hashlib, hmac, secrets
def hash_password(password: str) -> str:
    """使用 scrypt 哈希密码。"""
    salt = secrets.token_bytes(16); digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1); return f"scrypt${salt.hex()}${digest.hex()}"
def verify_password(password: str, encoded: str) -> bool:
    """校验密码。"""
    try:
        _, salt, digest = encoded.split("$", 2); actual = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=1); return hmac.compare_digest(actual.hex(), digest)
    except (ValueError, TypeError): return False
def token_digest(token: str) -> str:
    """生成令牌摘要。"""
    return hashlib.sha256(token.encode()).hexdigest()
