"""Tests for webhook signature verification."""

import hashlib
import hmac

import pytest

from gh_babysitter.server.signature import verify_signature


def test_valid_signature_is_accepted():
    body = b'{"zen":"Keep it logically awesome."}'
    signature = hmac.new(b"secret", body, hashlib.sha256).hexdigest()

    assert verify_signature("secret", body, f"sha256={signature}")


@pytest.mark.parametrize(
    "header",
    [
        None,
        "",
        "sha1=abc",
        "sha256=abc",
        f"sha256={'z' * 64}",
    ],
)
def test_missing_or_malformed_signature_is_rejected(header):
    assert not verify_signature("secret", b"body", header)


def test_wrong_signature_is_rejected():
    assert not verify_signature("secret", b"body", f"sha256={'0' * 64}")
