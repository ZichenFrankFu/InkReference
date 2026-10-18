"""Vault tests use invented keys; real Windows test owns one temporary entry."""

import json
import sys
import types

import httpx
import pytest

from inkreference import credentials
from inkreference.credentials import CredentialVault
from inkreference.database import DataError
from inkreference.models import ModelService


class MemoryNativeFixture:
    def __init__(self):
        self.entries = {}
        self.fail = False

    def get_password(self, service, account):
        if self.fail:
            raise RuntimeError("raw exception includes invented-super-secret")
        return self.entries.get((service, account))

    def set_password(self, service, account, key):
        if self.fail:
            raise RuntimeError("raw exception includes invented-super-secret")
        self.entries[service, account] = key

    def delete_password(self, service, account):
        if self.fail:
            raise RuntimeError("raw exception includes invented-super-secret")
        self.entries.pop((service, account), None)


def configured(service, **kwargs):
    return service.save_profile({"provider": "openai_compatible", "connection_mode": "remote", "base_url": "https://api.example/v1", "model": "test-model", "credential_mode": "vault", **kwargs})


@pytest.fixture
def native(monkeypatch):
    backend = MemoryNativeFixture()
    monkeypatch.setattr(credentials, "_native_backend", lambda: backend)
    return backend


def test_vault_secret_survives_restart_but_never_enters_application_files(tmp_path, native):
    service = ModelService(tmp_path)
    profile = configured(service, api_key="invented-super-secret")
    assert profile["has_vault_key"] and not profile["has_session_key"]
    assert profile["credential_status"] == "available" and profile["key_available"]
    requests = []
    restarted = ModelService(tmp_path, transport=httpx.MockTransport(lambda req: requests.append(req) or httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]})))
    restarted.test_profile(profile["id"], allow_remote=True)
    assert requests[0].headers["Authorization"] == "Bearer invented-super-secret"
    assert len(native.entries) == 1
    assert "invented-super-secret" not in json.dumps(restarted.list_profiles())
    assert "invented-super-secret" not in json.dumps(restarted.list_usage())
    for path in tmp_path.iterdir():
        if path.is_file():
            assert b"invented-super-secret" not in path.read_bytes()
    cleared = restarted.clear_credentials(profile["id"])
    assert not cleared["has_vault_key"] and not cleared["key_available"] and native.entries == {}


def test_workspace_and_endpoint_bound_vault_entries_cannot_follow_change(tmp_path, native):
    service = ModelService(tmp_path / "original")
    profile = configured(service, api_key="invented-super-secret")
    original_service = next(iter(native.entries))[0]
    other = CredentialVault(tmp_path / "copy")
    assert other.service != original_service
    changed = service.save_profile({"base_url": "https://next.example/v1"}, profile["id"])
    assert not changed["has_vault_key"] and not changed["key_available"]
    assert native.entries == {}


def test_failed_vault_backend_never_falls_back_to_plaintext_or_session(tmp_path, native):
    service = ModelService(tmp_path)
    native.fail = True
    with pytest.raises(DataError) as failed:
        configured(service, api_key="invented-super-secret")
    assert "invented-super-secret" not in str(failed.value)
    assert service.list_profiles() == [] and service._session_keys == {}
    assert native.entries == {}
    for path in tmp_path.iterdir():
        if path.is_file():
            assert b"invented-super-secret" not in path.read_bytes()


def test_unavailable_vault_allows_session_but_rejects_persistent_credentials(tmp_path, monkeypatch):
    monkeypatch.setattr(credentials, "_native_backend", lambda: None)
    service = ModelService(tmp_path)
    assert service.security_status()["vault_available"] is False
    with pytest.raises(DataError) as failed:
        configured(service, api_key="invented-super-secret")
    assert failed.value.status_code == 503
    profile = configured(service, credential_mode="session", api_key="invented-super-secret")
    assert profile["has_session_key"] and not profile["has_vault_key"]


def test_vault_draft_key_is_ephemeral_and_never_saved(tmp_path, native):
    service = ModelService(tmp_path, transport=httpx.MockTransport(lambda req: httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]})))
    service.test_draft({"provider": "openai_compatible", "base_url": "https://api.example/v1", "model": "test-model", "credential_mode": "vault", "api_key": "invented-super-secret", "remote_consent": True})
    assert native.entries == {} and service.list_profiles() == []


def test_deleting_profile_also_deletes_owned_vault_entry(tmp_path, native):
    service = ModelService(tmp_path)
    profile = configured(service, api_key="invented-super-secret")
    assert native.entries
    service.delete_profile(profile["id"])
    assert native.entries == {} and service.list_profiles() == []


def test_native_selection_ignores_configured_plaintext_backend(monkeypatch):
    monkeypatch.setenv("PYTHON_KEYRING_BACKEND", "keyrings.alt.file.PlaintextKeyring")
    monkeypatch.setattr(credentials.sys, "platform", "win32")
    visited = []
    native_class = type("WinVaultKeyring", (), {"__module__": "keyring.backends.Windows", "priority": 5})
    def load(name):
        visited.append(name)
        return types.SimpleNamespace(WinVaultKeyring=native_class)
    monkeypatch.setattr(credentials.importlib, "import_module", load)
    selected = credentials._native_backend()
    assert type(selected).__name__ == "WinVaultKeyring"
    assert visited == ["keyring.backends.Windows"]


def test_backend_class_from_third_party_is_rejected(monkeypatch):
    monkeypatch.setattr(credentials.sys, "platform", "win32")
    unsafe = type("WinVaultKeyring", (), {"__module__": "keyrings.alt.file", "priority": 99})
    monkeypatch.setattr(credentials.importlib, "import_module", lambda name: types.SimpleNamespace(WinVaultKeyring=unsafe))
    assert credentials._native_backend() is None


@pytest.mark.skipif(sys.platform != "win32", reason="actual Windows Credential Locker requires Windows")
def test_actual_windows_native_vault_round_trip_for_temporary_invented_key(tmp_path):
    vault = CredentialVault(tmp_path / "isolated-vault-test")
    if not vault.status()["vault_available"]:
        pytest.skip("Windows Credential Locker unavailable to this runner")
    profile_id, binding, key = "synthetic-test-only", "endpoint-binding-test-only", "inkreference-invented-fixture-key"
    try:
        vault.set(profile_id, binding, key)
        assert CredentialVault(tmp_path / "isolated-vault-test").get(profile_id, binding) == key
        assert CredentialVault(tmp_path / "different-workspace").get(profile_id, binding) is None
    finally:
        vault.delete(profile_id, binding)
    assert vault.get(profile_id, binding) is None


def test_locked_vault_read_never_transmits_or_exposes_backend_exception(tmp_path, native):
    requests = []
    service = ModelService(tmp_path, transport=httpx.MockTransport(lambda req: requests.append(req)))
    profile = configured(service, api_key="invented-super-secret")
    native.fail = True
    with pytest.raises(DataError) as failed:
        service.test_profile(profile["id"], allow_remote=True)
    assert failed.value.status_code == 503
    assert "invented-super-secret" not in str(failed.value)
    assert requests == [] and service.list_usage() == []
