"""
Криптографический модуль для I2PChat.

Предоставляет:
- HMAC для проверки целостности сообщений
- Шифрование/дешифрование через NaCl SecretBox
- Утилиты для handshake
"""

import hashlib
import hmac
import os
import secrets
from typing import Optional, Tuple

HMAC_SIZE = 32
NONCE_SIZE = 32


def generate_nonce() -> bytes:
    """Генерирует криптографически безопасный nonce (32 байта)."""
    return secrets.token_bytes(NONCE_SIZE)


def hkdf_extract(salt: bytes, ikm: bytes) -> bytes:
    """
    HKDF-Extract (RFC 5869) with HMAC-SHA256.
    """
    effective_salt = salt if salt else b"\x00" * 32
    return hmac.new(effective_salt, ikm, hashlib.sha256).digest()


def hkdf_expand(prk: bytes, info: bytes, length: int) -> bytes:
    """
    HKDF-Expand (RFC 5869) with HMAC-SHA256.
    """
    if length <= 0:
        raise ValueError("HKDF output length must be positive")
    okm = b""
    previous = b""
    counter = 1
    while len(okm) < length:
        previous = hmac.new(
            prk,
            previous + info + bytes([counter]),
            hashlib.sha256,
        ).digest()
        okm += previous
        counter += 1
        if counter > 255:
            raise ValueError("HKDF output too large")
    return okm[:length]


def derive_handshake_subkeys(
    dh_shared: bytes,
    nonce_init: bytes,
    nonce_resp: bytes,
) -> Tuple[bytes, bytes, bytes, bytes]:
    """
    Деривация направленных session subkeys (protocol v4 / HS4).

    Возвращает четыре независимых 32-байтных ключа:
    - k_enc_i2r, k_mac_i2r — трафик initiator → responder
    - k_enc_r2i, k_mac_r2i — трафик responder → initiator

    Направленное разделение ключей исключает reflection-атаку: кадр,
    отражённый обратно отправителю, проверяется ключом противоположного
    направления и не проходит MAC. Это НЕСОВМЕСТИМО с HS3 (v1.3.x): домены
    и число ключей изменены.
    """
    salt = hashlib.sha256(
        b"I2PCHAT-HS4-SALT|" + nonce_init + nonce_resp
    ).digest()
    prk = hkdf_extract(salt, dh_shared)
    k_enc_i2r = hkdf_expand(prk, b"I2PCHAT-HS4|key|enc|i2r", 32)
    k_mac_i2r = hkdf_expand(prk, b"I2PCHAT-HS4|key|mac|i2r", 32)
    k_enc_r2i = hkdf_expand(prk, b"I2PCHAT-HS4|key|enc|r2i", 32)
    k_mac_r2i = hkdf_expand(prk, b"I2PCHAT-HS4|key|mac|r2i", 32)
    return k_enc_i2r, k_mac_i2r, k_enc_r2i, k_mac_r2i


def compute_handshake_transcript_hash(resp_sig_payload: bytes) -> bytes:
    """
    Хэш транскрипта handshake для key confirmation (FINISHED).

    ``resp_sig_payload`` уже включает оба адреса, оба nonce, оба ephemeral
    и оба signing pubkey, т.е. является полным транскриптом обмена.
    """
    return hashlib.sha256(b"I2PCHAT-HS4-TRANSCRIPT|" + resp_sig_payload).digest()


def compute_handshake_finished(mac_key: bytes, transcript_hash: bytes) -> bytes:
    """HMAC-SHA256 подтверждения ключей (FINISHED) над transcript hash."""
    return hmac.new(
        mac_key, b"I2PCHAT-HS4|FINISHED|" + transcript_hash, hashlib.sha256
    ).digest()


def verify_handshake_finished(
    mac_key: bytes, transcript_hash: bytes, tag: bytes
) -> bool:
    """Проверяет FINISHED-подтверждение с защитой от timing attack."""
    expected = compute_handshake_finished(mac_key, transcript_hash)
    return hmac.compare_digest(expected, tag)


def compute_mac(
    key: bytes,
    msg_type: str,
    body: bytes,
    seq: Optional[int] = None,
    msg_id: Optional[int] = None,
    flags: Optional[int] = None,
) -> bytes:
    """
    Вычисляет HMAC-SHA256 для сообщения.
    
    Args:
        key: 32-байтный секретный ключ
        msg_type: тип сообщения (1 символ)
        body: тело сообщения
        seq: опциональный номер сообщения (anti-replay)
        msg_id: опциональный ID сообщения из заголовка vNext
        flags: опциональные флаги кадра из заголовка vNext

    Returns:
        32-байтный HMAC
    """
    # Явный UTF-8 для одинакового результата на всех платформах (Linux/Windows/macOS)
    type_bytes = msg_type.encode("utf-8") if isinstance(msg_type, str) else msg_type
    mac_input = type_bytes
    if seq is not None:
        # Фиксированное 8-байтное представление номера кадра.
        mac_input += int(seq).to_bytes(8, "big", signed=False)
    if flags is not None:
        # Фиксированное 1-байтное представление флагов vNext-заголовка.
        mac_input += int(flags & 0xFF).to_bytes(1, "big", signed=False)
    if msg_id is not None:
        # Фиксированное 8-байтное представление MSG_ID из заголовка.
        mac_input += int(msg_id).to_bytes(8, "big", signed=False)
    mac_input += body
    return hmac.new(key, mac_input, hashlib.sha256).digest()


def verify_mac(
    key: bytes,
    msg_type: str,
    body: bytes,
    mac: bytes,
    seq: Optional[int] = None,
    msg_id: Optional[int] = None,
    flags: Optional[int] = None,
) -> bool:
    """
    Проверяет HMAC сообщения с защитой от timing attack.
    
    Returns:
        True если MAC валиден
    """
    expected = compute_mac(key, msg_type, body, seq=seq, msg_id=msg_id, flags=flags)
    return hmac.compare_digest(expected, mac)


try:
    from nacl.secret import SecretBox
    from nacl.public import PrivateKey, PublicKey, Box
    from nacl.signing import SigningKey, VerifyKey
    from nacl.exceptions import CryptoError
    from nacl.encoding import RawEncoder
    
    NACL_AVAILABLE = True
    NACL_IMPORT_ERROR = ""

    def generate_signing_keypair() -> Tuple[bytes, bytes]:
        """
        Генерирует пару Ed25519 ключей для подписи handshake.

        Returns:
            (seed32, verify_key32)
        """
        sk = SigningKey.generate()
        return bytes(sk.encode()), bytes(sk.verify_key)

    def get_verify_key_from_seed(seed: bytes) -> bytes:
        """Возвращает 32-байтный verify_key по seed (32 байта). Для handshake pinning."""
        key = SigningKey(seed[:32])
        return bytes(key.verify_key)
    
    def encrypt_message(key: bytes, plaintext: bytes) -> bytes:
        """
        Шифрует сообщение с помощью NaCl SecretBox (XSalsa20-Poly1305).
        Nonce генерируется автоматически и включается в результат.
        
        Args:
            key: 32-байтный секретный ключ
            plaintext: данные для шифрования
            
        Returns:
            зашифрованные данные (nonce + ciphertext + tag)
        """
        box = SecretBox(key)
        return bytes(box.encrypt(plaintext))
    
    def decrypt_message(key: bytes, ciphertext: bytes) -> Optional[bytes]:
        """
        Дешифрует сообщение.
        
        Args:
            key: 32-байтный секретный ключ  
            ciphertext: зашифрованные данные
            
        Returns:
            расшифрованные данные или None при ошибке
        """
        try:
            box = SecretBox(key)
            return bytes(box.decrypt(ciphertext))
        except CryptoError:
            return None
    
    def generate_ephemeral_keypair() -> Tuple[bytes, bytes]:
        """
        Генерирует эфемерную пару ключей X25519 для DH.
        
        Returns:
            (private_key, public_key) - оба по 32 байта
        """
        private = PrivateKey.generate()
        public = private.public_key
        return bytes(private), bytes(public)
    
    def compute_dh_shared_secret(my_private: bytes, peer_public: bytes) -> bytes:
        """
        Вычисляет общий секрет через X25519 Diffie-Hellman.
        
        Args:
            my_private: мой приватный ключ (32 байта)
            peer_public: публичный ключ пира (32 байта)
            
        Returns:
            32-байтный shared secret

        Raises:
            ValueError: если публичный ключ пира невалиден (неверная длина,
            all-zero или low-order точка, дающая неконтрибутивный секрет).
        """
        if len(peer_public) != 32:
            raise ValueError("peer public key must be 32 bytes")
        # All-zero публичный ключ — тривиальная неконтрибутивная точка.
        # Прочие low-order точки Curve25519 отвергаются самим libsodium
        # (crypto_scalarmult возвращает ошибку для неконтрибутивного секрета);
        # оборачиваем это в явный ValueError на границе.
        if peer_public == bytes(32):
            raise ValueError("peer public key is all-zero (invalid X25519 point)")
        try:
            box = Box(PrivateKey(my_private), PublicKey(peer_public))
            shared = bytes(box.shared_key())
        except Exception as exc:
            raise ValueError(f"invalid X25519 public key: {exc}") from exc
        if shared == bytes(32):
            raise ValueError("X25519 produced an all-zero shared secret")
        return shared
    
    def sign_data(signing_key: bytes, data: bytes) -> bytes:
        """
        Подписывает данные с помощью Ed25519.
        
        Args:
            signing_key: 64-байтный seed Ed25519 ключа
            data: данные для подписи
            
        Returns:
            64-байтная подпись
        """
        key = SigningKey(signing_key[:32])
        return bytes(key.sign(data).signature)
    
    def verify_signature(verify_key: bytes, data: bytes, signature: bytes) -> bool:
        """
        Проверяет подпись Ed25519.
        
        Args:
            verify_key: 32-байтный публичный ключ
            data: подписанные данные
            signature: 64-байтная подпись
            
        Returns:
            True если подпись валидна
        """
        try:
            vk = VerifyKey(verify_key)
            vk.verify(data, signature)
            return True
        except Exception:
            return False

except ImportError as _nacl_err:
    NACL_AVAILABLE = False
    NACL_IMPORT_ERROR = str(_nacl_err)

    def get_verify_key_from_seed(seed: bytes) -> bytes:
        raise NotImplementedError("pynacl not installed")
    
    def generate_signing_keypair() -> Tuple[bytes, bytes]:
        raise NotImplementedError("pynacl not installed")
    
    def encrypt_message(key: bytes, plaintext: bytes) -> bytes:
        raise NotImplementedError("pynacl not installed")
    
    def decrypt_message(key: bytes, ciphertext: bytes) -> Optional[bytes]:
        raise NotImplementedError("pynacl not installed")
    
    def generate_ephemeral_keypair() -> Tuple[bytes, bytes]:
        raise NotImplementedError("pynacl not installed")
    
    def compute_dh_shared_secret(my_private: bytes, peer_public: bytes) -> bytes:
        raise NotImplementedError("pynacl not installed")
    
    def sign_data(signing_key: bytes, data: bytes) -> bytes:
        raise NotImplementedError("pynacl not installed")
    
    def verify_signature(verify_key: bytes, data: bytes, signature: bytes) -> bool:
        raise NotImplementedError("pynacl not installed")
