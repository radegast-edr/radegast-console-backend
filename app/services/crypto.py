import os
import secrets
import base64

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)


def generate_aes_key() -> str:
    """Generate a random 256-bit AES key, returned as base64."""
    return base64.b64encode(secrets.token_bytes(32)).decode()


def encrypt_aes_gcm(plaintext: str, key_b64: str) -> str:
    """Encrypt plaintext with AES-GCM. Returns base64(nonce + ciphertext)."""
    key = base64.b64decode(key_b64)
    aesgcm = AESGCM(key)
    nonce = os.urandom(12)
    ciphertext = aesgcm.encrypt(nonce, plaintext.encode(), None)
    return base64.b64encode(nonce + ciphertext).decode()


def decrypt_aes_gcm(encrypted_b64: str, key_b64: str) -> str | None:
    """Decrypt AES-GCM encrypted data. Returns None on failure."""
    try:
        key = base64.b64decode(key_b64)
        data = base64.b64decode(encrypted_b64)
        nonce = data[:12]
        ciphertext = data[12:]
        aesgcm = AESGCM(key)
        plaintext = aesgcm.decrypt(nonce, ciphertext, None)
        return plaintext.decode()
    except Exception:
        return None


def generate_age_keypair() -> tuple[str, str]:
    """Generate an AGE keypair. Returns (public_key, private_key)."""
    from ssage import SSAGE
    private_key = SSAGE.generate_private_key()
    s = SSAGE(private_key)
    return s.public_key, private_key


def age_encrypt(plaintext: str, *public_keys: str) -> str:
    """Encrypt plaintext for multiple AGE public key recipients."""
    from ssage import SSAGE
    if not public_keys:
        raise ValueError("At least one public key required")
    s = SSAGE(public_key=public_keys[0])
    additional = list(public_keys[1:]) if len(public_keys) > 1 else None
    return s.encrypt(plaintext, additional_recipients=additional)


def age_decrypt(ciphertext: str, private_key: str) -> str | None:
    """Decrypt AGE ciphertext with private key."""
    try:
        from ssage import SSAGE
        s = SSAGE(private_key)
        return s.decrypt(ciphertext)
    except Exception:
        return None


def generate_ed25519_keypair() -> tuple[str, str]:
    """Generate an Ed25519 signing keypair. Returns (public_key_b64, private_key_b64)."""
    private_key = Ed25519PrivateKey.generate()
    private_bytes = private_key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    public_bytes = private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return base64.b64encode(public_bytes).decode(), base64.b64encode(private_bytes).decode()


def ed25519_sign(message: bytes, private_key_b64: str) -> str:
    """Sign a message with Ed25519. Returns base64 signature."""
    private_bytes = base64.b64decode(private_key_b64)
    private_key = Ed25519PrivateKey.from_private_bytes(private_bytes)
    signature = private_key.sign(message)
    return base64.b64encode(signature).decode()


def ed25519_verify(message: bytes, signature_b64: str, public_key_b64: str) -> bool:
    """Verify an Ed25519 signature. Returns True if valid."""
    try:
        public_bytes = base64.b64decode(public_key_b64)
        public_key = Ed25519PublicKey.from_public_bytes(public_bytes)
        signature = base64.b64decode(signature_b64)
        public_key.verify(signature, message)
        return True
    except Exception:
        return False
