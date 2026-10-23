"""The vault: real AES-256-GCM round-trips over tmp files (C7-style:
the crypto is never mocked — only the filesystem location is a tmp dir).
"""

import base64
import json
import os
import stat
from pathlib import Path

import pytest

from school.domain import Found, Missing
from school.vault import Vault, VaultCredentials, VaultError

ACCOUNT = "samuel.smith26@k12.leanderisd.org"
USERNAME = "samuel.smith26@k12.leanderisd.org"
PASSWORD = "correct horse battery staple"


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    instance = Vault(
        vault_path=tmp_path / "vault.json", key_path=tmp_path / "vault.key"
    )
    instance.unlock()
    return instance


def test_first_unlock_creates_key_with_0600_and_empty_vault(
    tmp_path: Path,
) -> None:
    key_path = tmp_path / "vault.key"
    vault_path = tmp_path / "vault.json"
    vault = Vault(vault_path=vault_path, key_path=key_path)
    vault.unlock()
    assert stat.S_IMODE(os.stat(key_path).st_mode) == 0o600
    assert len(key_path.read_bytes()) == 32
    payload = json.loads(vault_path.read_text(encoding="utf-8"))
    assert payload == {"version": 1, "entries": {}}
    assert vault.names() == []


def test_round_trip_and_persistence_across_instances(
    tmp_path: Path,
) -> None:
    first = Vault(
        vault_path=tmp_path / "vault.json", key_path=tmp_path / "vault.key"
    )
    first.unlock()
    first.put("acct.username", USERNAME)
    first.put("acct.password", PASSWORD)

    second = Vault(
        vault_path=tmp_path / "vault.json", key_path=tmp_path / "vault.key"
    )
    second.unlock()
    assert second.names() == ["acct.password", "acct.username"]
    assert second.get("acct.username") == Found(value=USERNAME)
    assert second.get("acct.password") == Found(value=PASSWORD)


def test_get_absent_name_is_missing_not_error(vault: Vault) -> None:
    result = vault.get("nope")
    assert isinstance(result, Missing)
    assert "nope" in result.reason


def test_put_replaces_and_delete_removes(vault: Vault) -> None:
    vault.put("k", "one")
    vault.put("k", "two")
    assert vault.get("k") == Found(value="two")
    vault.delete("k")
    assert isinstance(vault.get("k"), Missing)
    vault.delete("k")  # deleting an absent name is not an error
    assert vault.names() == []


def test_secrets_are_not_plaintext_on_disk(
    vault: Vault, tmp_path: Path
) -> None:
    vault.put("acct.password", PASSWORD)
    raw = (tmp_path / "vault.json").read_text(encoding="utf-8")
    assert PASSWORD not in raw
    assert base64.b64encode(PASSWORD.encode()).decode() not in raw


def test_tampered_ciphertext_raises_vault_error(
    vault: Vault, tmp_path: Path
) -> None:
    vault.put("acct.password", PASSWORD)
    vault_file = tmp_path / "vault.json"
    payload = json.loads(vault_file.read_text(encoding="utf-8"))
    encoded = payload["entries"]["acct.password"]["ciphertext"]
    raw = bytearray(base64.b64decode(encoded))
    raw[0] ^= 0xFF  # flip one ciphertext byte
    payload["entries"]["acct.password"]["ciphertext"] = base64.b64encode(
        bytes(raw)
    ).decode()
    vault_file.write_text(json.dumps(payload), encoding="utf-8")

    fresh = Vault(vault_path=vault_file, key_path=tmp_path / "vault.key")
    fresh.unlock()  # structure is fine; decryption must be what fails
    with pytest.raises(VaultError, match="authentication"):
        fresh.get("acct.password")


def test_wrong_key_raises_vault_error(tmp_path: Path) -> None:
    vault = Vault(
        vault_path=tmp_path / "vault.json", key_path=tmp_path / "vault.key"
    )
    vault.unlock()
    vault.put("acct.password", PASSWORD)
    (tmp_path / "other.key").write_bytes(os.urandom(32))
    impostor = Vault(
        vault_path=tmp_path / "vault.json", key_path=tmp_path / "other.key"
    )
    impostor.unlock()
    with pytest.raises(VaultError):
        impostor.get("acct.password")


def test_verify_passes_for_a_healthy_vault(vault: Vault) -> None:
    vault.put("acct.password", PASSWORD)
    vault.verify()  # must not raise


def test_verify_on_an_empty_vault_passes(vault: Vault) -> None:
    vault.verify()  # nothing a wrong key could be wrong about yet


def test_verify_raises_for_a_wrong_key(tmp_path: Path) -> None:
    """M7 D4 enabler: verify() is the boot-time decrypt-everything
    probe — where get() fails per-read, verify fails up front."""
    vault = Vault(
        vault_path=tmp_path / "vault.json", key_path=tmp_path / "vault.key"
    )
    vault.unlock()
    vault.put("acct.password", PASSWORD)
    (tmp_path / "other.key").write_bytes(os.urandom(32))
    impostor = Vault(
        vault_path=tmp_path / "vault.json", key_path=tmp_path / "other.key"
    )
    impostor.unlock()  # structure is fine; only decryption can tell
    with pytest.raises(VaultError, match="authentication"):
        impostor.verify()


def test_verify_before_unlock_raises(tmp_path: Path) -> None:
    vault = Vault(
        vault_path=tmp_path / "vault.json", key_path=tmp_path / "vault.key"
    )
    with pytest.raises(VaultError, match="unlock"):
        vault.verify()


def test_ciphertext_cannot_move_between_entries(
    vault: Vault, tmp_path: Path
) -> None:
    vault.put("a.password", "secret-a")
    vault.put("b.password", "secret-b")
    vault_file = tmp_path / "vault.json"
    payload = json.loads(vault_file.read_text(encoding="utf-8"))
    payload["entries"]["b.password"] = payload["entries"]["a.password"]
    vault_file.write_text(json.dumps(payload), encoding="utf-8")
    fresh = Vault(vault_path=vault_file, key_path=tmp_path / "vault.key")
    fresh.unlock()
    with pytest.raises(VaultError):
        fresh.get("b.password")  # entry name is AES-GCM associated data


def test_operations_before_unlock_raise(tmp_path: Path) -> None:
    vault = Vault(
        vault_path=tmp_path / "vault.json", key_path=tmp_path / "vault.key"
    )
    with pytest.raises(VaultError, match="unlock"):
        vault.get("anything")
    with pytest.raises(VaultError, match="unlock"):
        vault.put("k", "v")
    with pytest.raises(VaultError, match="unlock"):
        vault.names()
    with pytest.raises(VaultError, match="unlock"):
        vault.delete("k")


def test_bad_key_length_is_a_vault_error(tmp_path: Path) -> None:
    (tmp_path / "vault.key").write_bytes(b"too short")
    vault = Vault(
        vault_path=tmp_path / "vault.json", key_path=tmp_path / "vault.key"
    )
    with pytest.raises(VaultError, match="32"):
        vault.unlock()


@pytest.mark.parametrize(
    "contents",
    [
        "not json at all",
        "[]",
        '{"version": 2, "entries": {}}',
        '{"version": 1}',
        '{"version": 1, "entries": {"a": 42}}',
        '{"version": 1, "entries": {"a": {"nonce": 5, "ciphertext": "x"}}}',
        '{"version": 1, "entries": {"a": {"nonce": "!!!", "ciphertext": "eA=="}}}',
        '{"version": 1, "entries": {"a": {"nonce": "eA==", "ciphertext": "eA=="}}}',
    ],
)
def test_corrupt_vault_files_raise_vault_error(
    tmp_path: Path, contents: str
) -> None:
    (tmp_path / "vault.json").write_text(contents, encoding="utf-8")
    vault = Vault(
        vault_path=tmp_path / "vault.json", key_path=tmp_path / "vault.key"
    )
    with pytest.raises(VaultError):
        vault.unlock()


def test_unicode_secrets_round_trip(vault: Vault) -> None:
    vault.put("greeting", "pässwörd ✓ 123")
    assert vault.get("greeting") == Found(value="pässwörd ✓ 123")


class TestVaultCredentials:
    def test_pair_returned_when_both_halves_present(
        self, vault: Vault
    ) -> None:
        vault.put(f"{ACCOUNT}.username", USERNAME)
        vault.put(f"{ACCOUNT}.password", PASSWORD)
        result = VaultCredentials(vault).credentials_for(ACCOUNT)
        assert result == Found(value=(USERNAME, PASSWORD))

    def test_missing_account_is_missing(self, vault: Vault) -> None:
        result = VaultCredentials(vault).credentials_for(ACCOUNT)
        assert isinstance(result, Missing)
        assert ACCOUNT in result.reason

    def test_missing_password_half_is_named(self, vault: Vault) -> None:
        vault.put(f"{ACCOUNT}.username", USERNAME)
        result = VaultCredentials(vault).credentials_for(ACCOUNT)
        assert isinstance(result, Missing)
        assert "password" in result.reason
