"""Encrypted credential store: the re-auth fallback's secret keeper.

Pattern: encrypted-at-rest Adapter over two files. The vault file is a
JSON document ``{"version": 1, "entries": {name: {nonce, ciphertext}}}``
(base64 fields); the key file is 32 raw bytes, kept SEPARATE from the
vault file. In production both live in a service-user-only directory;
the key file is the secret — whoever holds it (and only it) can read
every entry.

Design substitution (approved at M6 dispatch, superseding DESIGN §3's
"age-encrypted file"): envelope encryption with AES-256-GCM from the
``cryptography`` package instead of an external ``age`` binary —
self-contained (no binary to provision on hal9k) and fully testable.
Each entry is encrypted under the vault key with a fresh 96-bit nonce
and the entry NAME as associated data, so a ciphertext transplanted
to a different entry name fails authentication too.

Failure posture: a wrong key, a tampered ciphertext, or a corrupt
vault file raises ``VaultError`` — never a silent empty result. Reads
of absent names are not errors: they return ``Missing`` (C1).
"""

import base64
import json
import os
from dataclasses import dataclass
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from school.domain import Found, Lookup, Missing

_FORMAT_VERSION = 1
_KEY_BYTES = 32
_NONCE_BYTES = 12


class VaultError(Exception):
    """The vault cannot be trusted or used: wrong key, tampered or
    corrupt contents, bad key material, or use before ``unlock()``."""


def _b64encode(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _b64decode(text: str) -> bytes:
    return base64.b64decode(text.encode("ascii"), validate=True)


@dataclass(frozen=True, kw_only=True)
class _Entry:
    nonce: bytes
    ciphertext: bytes


class Vault:
    """An AES-256-GCM encrypted name -> secret store over two files.

    Construct with the vault/key paths, then ``unlock()`` once before
    use. ``unlock`` creates the key file (0600) and an empty vault on
    first use — first unlock IS vault creation.
    """

    def __init__(self, *, vault_path: Path, key_path: Path) -> None:
        self._vault_path = vault_path
        self._key_path = key_path
        self._key: bytes | None = None
        self._entries: dict[str, _Entry] = {}

    # -- lifecycle ----------------------------------------------------

    def unlock(self) -> None:
        """Load (or create) the key and vault; ``VaultError`` on any
        sign the files are corrupt or the key does not fit the vault."""
        self._key = self._load_or_create_key()
        self._entries = self._load_entries()

    # -- operations ---------------------------------------------------

    def put(self, name: str, secret: str) -> None:
        """Store ``secret`` under ``name``, replacing any old value."""
        key = self._require_key()
        nonce = os.urandom(_NONCE_BYTES)
        ciphertext = AESGCM(key).encrypt(
            nonce, secret.encode("utf-8"), name.encode("utf-8")
        )
        self._entries[name] = _Entry(nonce=nonce, ciphertext=ciphertext)
        self._persist()

    def get(self, name: str) -> Lookup[str]:
        """The secret stored under ``name`` (``Missing`` when absent).

        A present-but-undecryptable entry is tampering or a wrong key:
        ``VaultError``, never a silent miss (see module docstring).
        """
        key = self._require_key()
        entry = self._entries.get(name)
        if entry is None:
            return Missing(reason=f"no vault entry named {name!r}")
        try:
            plaintext = AESGCM(key).decrypt(
                entry.nonce, entry.ciphertext, name.encode("utf-8")
            )
        except InvalidTag as exc:
            raise VaultError(
                f"vault entry {name!r} failed authentication "
                "(tampered contents or wrong key)"
            ) from exc
        return Found(value=plaintext.decode("utf-8"))

    def names(self) -> list[str]:
        """Every entry name, deterministically ordered."""
        self._require_key()
        return sorted(self._entries)

    def delete(self, name: str) -> None:
        """Remove ``name`` if present (absent names are not an error)."""
        self._require_key()
        if name in self._entries:
            del self._entries[name]
            self._persist()

    # -- internals ----------------------------------------------------

    def _require_key(self) -> bytes:
        if self._key is None:
            raise VaultError("vault is locked: call unlock() first")
        return self._key

    def _load_or_create_key(self) -> bytes:
        if not self._key_path.exists():
            key = AESGCM.generate_key(bit_length=256)
            # O_EXCL + explicit mode: the key file is born 0600 and we
            # never truncate someone else's key by racing them.
            fd = os.open(
                self._key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
            )
            try:
                os.write(fd, key)
            finally:
                os.close(fd)
            return key
        key = self._key_path.read_bytes()
        if len(key) != _KEY_BYTES:
            raise VaultError(
                f"vault key at {self._key_path} is {len(key)} bytes, "
                f"expected {_KEY_BYTES}"
            )
        return key

    def _load_entries(self) -> dict[str, _Entry]:
        if not self._vault_path.exists():
            self._entries = {}
            self._persist()
            return {}
        try:
            payload: object = json.loads(
                self._vault_path.read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError) as exc:
            raise VaultError(
                f"vault file at {self._vault_path} is unreadable: {exc}"
            ) from exc
        if not isinstance(payload, dict):
            raise VaultError("vault file is not a JSON object")
        if payload.get("version") != _FORMAT_VERSION:
            raise VaultError(
                f"unsupported vault version: {payload.get('version')!r}"
            )
        raw_entries = payload.get("entries")
        if not isinstance(raw_entries, dict):
            raise VaultError("vault file carries no 'entries' object")
        entries: dict[str, _Entry] = {}
        for name, raw in raw_entries.items():
            entries[name] = self._decode_entry(name, raw)
        return entries

    def _decode_entry(self, name: str, raw: object) -> _Entry:
        if not isinstance(raw, dict):
            raise VaultError(f"vault entry {name!r} is not an object")
        nonce_text = raw.get("nonce")
        ciphertext_text = raw.get("ciphertext")
        if not isinstance(nonce_text, str) or not isinstance(
            ciphertext_text, str
        ):
            raise VaultError(
                f"vault entry {name!r} lacks base64 nonce/ciphertext"
            )
        try:
            nonce = _b64decode(nonce_text)
            ciphertext = _b64decode(ciphertext_text)
        except ValueError as exc:
            raise VaultError(
                f"vault entry {name!r} is not valid base64"
            ) from exc
        if len(nonce) != _NONCE_BYTES:
            raise VaultError(
                f"vault entry {name!r} has a {len(nonce)}-byte nonce"
            )
        return _Entry(nonce=nonce, ciphertext=ciphertext)

    def _persist(self) -> None:
        payload = {
            "version": _FORMAT_VERSION,
            "entries": {
                name: {
                    "nonce": _b64encode(entry.nonce),
                    "ciphertext": _b64encode(entry.ciphertext),
                }
                for name, entry in sorted(self._entries.items())
            },
        }
        text = json.dumps(payload, indent=2, sort_keys=True)
        # Write-then-replace: a crash mid-write leaves the old vault.
        tmp_path = self._vault_path.with_suffix(".tmp")
        tmp_path.write_text(text, encoding="utf-8")
        os.replace(tmp_path, self._vault_path)


class VaultCredentials:
    """Adapter: per-account username/password pairs out of a Vault.

    Pattern: Adapter (the re-auth helper consumes this shape, never
    raw vault entries). Entries are named ``<account_id>.username``
    and ``<account_id>.password``.
    """

    def __init__(self, vault: Vault) -> None:
        self._vault = vault

    def credentials_for(
        self, account_id: str
    ) -> Lookup[tuple[str, str]]:
        """The (username, password) pair for ``account_id``; ``Missing``
        naming whichever half is absent."""
        username = self._vault.get(f"{account_id}.username")
        password = self._vault.get(f"{account_id}.password")
        match (username, password):
            case (Found(value=user), Found(value=secret)):
                return Found(value=(user, secret))
            case (Missing(reason=reason), _):
                return Missing(
                    reason=f"no credentials for {account_id!r}: {reason}"
                )
            case (_, Missing(reason=reason)):
                return Missing(
                    reason=f"no credentials for {account_id!r}: {reason}"
                )
            case _:  # pragma: no cover - the cases above are exhaustive
                raise AssertionError("unreachable Lookup combination")
