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


def compute_shared_key(nonce_a: bytes, nonce_b: bytes) -> bytes:
    """
    Вычисляет общий ключ из двух nonce.
    shared_key = SHA256(nonce_A || nonce_B)
    """
    return hashlib.sha256(nonce_a + nonce_b).digest()


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
        """
        box = Box(PrivateKey(my_private), PublicKey(peer_public))
        return bytes(box.shared_key())
    
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
