"""OS credential storage with explicit native backends and no file fallback.

Only the official keyring Windows/macOS/SecretService/KWallet implementations
are loaded. In particular, entry-point discovery, configured third-party
backends, plaintext stores and chained fallbacks are never used. The keyring
service is tied to this workspace path, so a copied project cannot silently
reuse another project's saved key.
"""

from __future__ import annotations

import hashlib
import importlib
import os
import sys
from pathlib import Path
from typing import Any

from .database import DataError


def _native_backend() -> Any | None:
    if sys.platform == "win32":
        candidates = [("keyring.backends.Windows", "WinVaultKeyring")]
    elif sys.platform == "darwin":
        candidates = [("keyring.backends.macOS", "Keyring")]
    elif sys.platform.startswith("linux"):
        candidates = [
            ("keyring.backends.SecretService", "Keyring"),
            ("keyring.backends.kwallet", "DBusKeyring"),
        ]
    else:
        candidates = []
    for module_name, class_name in candidates:
        try:
            backend_class = getattr(importlib.import_module(module_name), class_name)
            # Do not accept a monkey-patched wrapper from a third-party module.
            if backend_class.__module__ != module_name:
                continue
            if backend_class.priority > 0:
                return backend_class()
        except Exception:
            # OS/keyring errors may include user names, paths or key values.
            continue
    return None


class CredentialVault:
    def __init__(self, data_dir: str | Path):
        path = os.path.normcase(str(Path(data_dir).resolve()))
        self.service = "InkReference:" + hashlib.sha256(path.encode("utf-8")).hexdigest()
        self._backend: Any = None
        self._checked = False

    def _resolve(self) -> Any | None:
        if not self._checked:
            self._backend = _native_backend()
            self._checked = True
        return self._backend

    def status(self) -> dict[str, Any]:
        backend = self._resolve()
        return {
            "vault_available": backend is not None,
            "vault_backend": type(backend).__module__ + "." + type(backend).__name__ if backend is not None else None,
            "vault_reason": None if backend is not None else "系统凭据库不可用，请使用本次会话或环境变量",
        }

    def _required(self) -> Any:
        backend = self._resolve()
        if backend is None:
            raise DataError(503, "系统凭据库不可用；密钥未写入文件，请改用本次会话或环境变量")
        return backend

    @staticmethod
    def account(profile_id: str, binding: str) -> str:
        return hashlib.sha256((profile_id + "\0" + binding).encode("utf-8")).hexdigest()

    def get(self, profile_id: str, binding: str) -> str | None:
        try:
            return self._required().get_password(self.service, self.account(profile_id, binding))
        except DataError:
            raise
        except Exception:
            raise DataError(503, "无法读取系统凭据库，请解锁凭据库后重试") from None

    def set(self, profile_id: str, binding: str, key: str) -> None:
        try:
            backend = self._required()
            backend.set_password(self.service, self.account(profile_id, binding), key)
            # Some degenerate backends silently ignore writes. Fail closed.
            if backend.get_password(self.service, self.account(profile_id, binding)) != key:
                raise DataError(503, "系统凭据库未确认保存；密钥未写入应用文件")
        except DataError:
            raise
        except Exception:
            raise DataError(503, "无法保存到系统凭据库；密钥未写入应用文件") from None

    def delete(self, profile_id: str, binding: str) -> None:
        try:
            backend = self._required()
            account = self.account(profile_id, binding)
            if backend.get_password(self.service, account) is not None:
                backend.delete_password(self.service, account)
            if backend.get_password(self.service, account) is not None:
                raise DataError(503, "系统凭据库未确认移除，请重试")
        except DataError:
            raise
        except Exception:
            raise DataError(503, "无法移除系统凭据库中的密钥，请解锁后重试") from None
