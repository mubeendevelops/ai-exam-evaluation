"""tests/test_api/test_cors.py — CORS_ORIGINS defaults and the preflight
behaviour they produce (Hardening pass 2026-09-06).

Three things pinned here, none of them covered before this pass:

  1. An unconfigured CORS policy REFUSES TO BOOT outside development — no
     more silent '*' fallback (api/settings.py::Settings.cors_origin_list).
  2. Development gets a USABLE default (the local Vite origin) rather than
     also failing, so a bare checkout still runs.
  3. The '*' + named-origins misconfiguration api/main.py::_configure_cors
     already refused (Hardening pass 2026-09-05) still refuses it — this
     pass changed the DEFAULT that logic sees, not the logic itself.

None of this touches identity or the database, so these tests skip the usual
`make_client`/`_dotenv` fixtures entirely — a bad CORS config must fail before
any of that machinery is even reachable.
"""
from __future__ import annotations

import httpx
import pytest

from api.main import create_app
from api.settings import Settings


def test_startup_raises_when_cors_origins_unset_outside_development():
    """The permissive '*' fallback is gone. A production-shaped app with no
    CORS_ORIGINS must refuse to build at all, not serve with a policy nobody
    configured."""
    settings = Settings(api_env="production", jwt_secret="a signing key for this test")

    with pytest.raises(RuntimeError, match="CORS_ORIGINS is not set"):
        create_app(settings)


def test_development_defaults_to_the_local_vite_origin():
    """Unset CORS_ORIGINS in development resolves to a usable default rather
    than also refusing to boot — same shape as JWT_SECRET's dev fallback."""
    settings = Settings(api_env="development", jwt_secret="a signing key for this test")

    app = create_app(settings)  # must not raise

    assert settings.cors_origin_list == ["http://localhost:5173"]
    assert app is not None


def test_startup_raises_when_cors_origins_mixes_wildcard_with_named_origins():
    """The '*' + named-origins misconfiguration is still refused — this pass
    only changed what an UNSET value resolves to, not this check."""
    settings = Settings(
        api_env="production", jwt_secret="a signing key for this test",
        cors_origins="*,https://allowed.example",
    )

    with pytest.raises(ValueError, match="mixes"):
        create_app(settings)


@pytest.fixture
def named_origin_app():
    """A production-shaped app with exactly one named CORS origin — real
    enough to exercise CORSMiddleware's actual preflight behaviour, not just
    the settings-parsing layer the tests above cover."""
    settings = Settings(
        api_env="production", jwt_secret="a signing key for this test",
        cors_origins="https://allowed.example",
    )
    return create_app(settings)


@pytest.mark.asyncio
async def test_preflight_from_an_unlisted_origin_is_refused(named_origin_app):
    """A browser preflight from an origin NOT in CORS_ORIGINS gets no
    `Access-Control-Allow-Origin` — CORSMiddleware answers such a preflight
    400 with the header absent, which is what makes the browser refuse the
    real request client-side. This is the property the whole hardening item
    exists to guarantee: an origin nobody configured must not be able to read
    a credentialed response.
    """
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=named_origin_app),
        base_url="http://testserver",
    ) as client:
        response = await client.options(
            "/api/v1/health",
            headers={
                "Origin": "https://evil.example",
                "Access-Control-Request-Method": "GET",
            },
        )

    assert response.status_code == 400, response.text
    assert "access-control-allow-origin" not in response.headers


@pytest.mark.asyncio
async def test_preflight_from_the_named_origin_succeeds(named_origin_app):
    """The configured origin gets a real preflight grant, WITH credentials —
    `_configure_cors` only enables `allow_credentials` when origins are
    actually named (Hardening pass 2026-09-05), which this proves still
    holds under the new default-resolution logic."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=named_origin_app),
        base_url="http://testserver",
    ) as client:
        response = await client.options(
            "/api/v1/health",
            headers={
                "Origin": "https://allowed.example",
                "Access-Control-Request-Method": "GET",
            },
        )

    assert response.status_code == 200, response.text
    assert response.headers["access-control-allow-origin"] == "https://allowed.example"
    assert response.headers["access-control-allow-credentials"] == "true"
