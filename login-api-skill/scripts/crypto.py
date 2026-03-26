"""
Tax Login Skill - 工具函数

提供MD5、Base64、RSA加密等通用工具
"""

import hashlib
import base64

try:
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import padding
except ModuleNotFoundError:  # pragma: no cover - 仅在缺少依赖的环境触发
    default_backend = None
    serialization = None
    padding = None


def md5(input_string: str) -> str:
    """
    计算字符串的MD5值

    Args:
        input_string: 输入字符串

    Returns:
        MD5哈希值（32位小写十六进制）
    """
    md = hashlib.md5()
    md.update(input_string.encode('utf-8'))
    return md.hexdigest()


def base64_encode(input_string: str) -> str:
    """
    Base64编码

    Args:
        input_string: 输入字符串

    Returns:
        Base64编码后的字符串
    """
    input_bytes = input_string.encode('utf-8')
    return base64.b64encode(input_bytes).decode('utf-8')


def rsa_encrypt(data: str, public_key_base64: str) -> str:
    """
    RSA公钥加密（支持分段加密）

    Args:
        data: 要加密的原始字符串
        public_key_base64: Base64编码的RSA公钥

    Returns:
        Base64编码的加密结果
    """
    if serialization is None or padding is None or default_backend is None:
        raise ModuleNotFoundError(
            "缺少 cryptography 依赖，无法执行 RSA 加密。"
            "请先安装 `cryptography>=3.4.0`。"
        )

    # 解码Base64公钥并加载
    public_key_bytes = base64.b64decode(public_key_base64)
    pub_key = serialization.load_der_public_key(
        public_key_bytes,
        backend=default_backend()
    )

    # 分段加密参数
    CHUNK_SIZE = 117  # RSA每次加密的最大字节数 (PKCS#1 v1.5)
    data_bytes = data.encode('utf-8')
    encrypted_chunks = []

    # 分段加密
    for offset in range(0, len(data_bytes), CHUNK_SIZE):
        chunk = data_bytes[offset:offset + CHUNK_SIZE]
        encrypted_chunk = pub_key.encrypt(
            chunk,
            padding.PKCS1v15()
        )
        encrypted_chunks.append(encrypted_chunk)

    # 合并所有加密段并Base64编码
    encrypted_data = b''.join(encrypted_chunks)
    return base64.b64encode(encrypted_data).decode('utf-8')


def build_signature(method: str, content_md5: str, req_date: str,
                    access_token: str, app_secret: str,
                    app_key: str) -> str:
    """
    构建API请求签名

    签名算法：
    1. to_sign = "{method}_{content_md5}_{req_date}_{access_token}_{app_secret}"
    2. signature = Base64(MD5(to_sign))
    3. req_sign = "API-SV1:{app_key}:{signature}"

    Args:
        method: HTTP方法（POST）
        content_md5: 请求体MD5
        req_date: 请求时间戳（毫秒）
        access_token: 访问令牌
        app_secret: 应用密钥
        app_key: 应用标识

    Returns:
        完整的请求签名
    """
    to_sign = f"{method}_{content_md5}_{req_date}_{access_token}_{app_secret}"
    md5_result = md5(to_sign)
    base64_result = base64_encode(md5_result)
    return f"API-SV1:{app_key}:{base64_result}"
