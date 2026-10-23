"""Encrypted credential store and browser-profile lifecycle support.

Patterns: encrypted-at-rest Adapter (AES-256-GCM vault over two files);
``VaultCredentials`` adapts raw entries to the (username, password)
shape the Classroom re-auth helper consumes.
"""

from school.vault.vault import Vault, VaultCredentials, VaultError

__all__ = [
    "Vault",
    "VaultCredentials",
    "VaultError",
]
