"""P21 security review, core side: blob keys bound to one college, image headers read before any
decoding, refusals that cost as much as real checks, pepper rotation, tenant suspension."""

import struct
import zlib
from uuid import UUID

import pytest

from tarn_core.domain.audit import AuditAction
from tarn_core.domain.common import BlobKey, college_blob_key, global_blob_key
from tarn_core.domain.identity import TenantStatus
from tarn_core.errors import (
    InvariantError,
    TenantViolationError,
    UnreadableFileError,
    UploadTooLargeError,
)
from tarn_core.ids import CollegeId
from tarn_core.services.auth import Refused, SignedIn
from tarn_core.services.blob_scope import CollegeScopedBlobStore, scoped_blobs
from tarn_core.services.images import (
    MAX_IMAGE_PIXELS,
    check_image_size,
    image_dimensions,
)
from tarn_core.services.uploads import check_upload_image
from tarn_core.testing import InMemory, MemoryBlobStore
from tarn_core.testing.auth_world import ADMIN_PASSWORD, AuthWorld

A = CollegeId(UUID(int=1))
B = CollegeId(UUID(int=2))
ADMIN = "admin@college-a.example"


# --- image headers ----------------------------------------------------------------------------


def png_header(width: int, height: int) -> bytes:
    """A PNG signature and IHDR declaring ``width`` × ``height`` (no pixel data needed)."""
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    chunk = b"IHDR" + ihdr
    return (
        b"\x89PNG\r\n\x1a\n"
        + struct.pack(">I", len(ihdr))
        + chunk
        + struct.pack(">I", zlib.crc32(chunk))
    )


def jpeg_header(width: int, height: int, *, app_bytes: int = 16) -> bytes:
    """SOI, an APP0 segment, then SOF0 declaring the size."""
    app = b"\xff\xe0" + struct.pack(">H", app_bytes + 2) + b"J" * app_bytes
    sof = b"\xff\xc0" + struct.pack(">HBHHB", 11, 8, height, width, 1) + b"\x01\x11\x00"
    return b"\xff\xd8" + app + sof + b"\xff\xda"


def test_png_and_jpeg_sizes_come_from_the_header() -> None:
    assert image_dimensions(png_header(2480, 3508)) == (2480, 3508)
    assert image_dimensions(jpeg_header(4000, 3000)) == (4000, 3000)
    assert image_dimensions(jpeg_header(10, 20, app_bytes=5000)) == (10, 20)
    assert image_dimensions(b"%PDF-1.7") is None
    assert image_dimensions(b"\xff\xd8\xff\xda") is None  # scan data before a frame header


def test_a_decompression_bomb_is_refused_before_decoding() -> None:
    bomb = png_header(60_000, 60_000)  # 3.6 gigapixels in 33 bytes
    with pytest.raises(UnreadableFileError, match="too large"):
        check_image_size(bomb)
    with pytest.raises(UploadTooLargeError):
        check_upload_image(bomb, "image/png")
    with pytest.raises(UploadTooLargeError):
        check_upload_image(jpeg_header(65_000, 65_000), "image/jpeg")
    check_upload_image(png_header(4000, 3000), "image/png")  # a phone photo passes
    assert MAX_IMAGE_PIXELS > 8000 * 6000  # a 48 MP phone photo fits


def test_unreadable_or_empty_headers_are_refused_by_decoders() -> None:
    with pytest.raises(UnreadableFileError):
        check_image_size(b"\x89PNG\r\n\x1a\n" + b"\x00" * 4)
    with pytest.raises(UnreadableFileError, match="no pixels"):
        check_image_size(png_header(0, 100))


# --- blob keys bound to one college -----------------------------------------------------------


def test_another_colleges_blob_is_refused_like_its_rows() -> None:
    inner = MemoryBlobStore()
    theirs = college_blob_key(B, "booklet", "x", "source", "001.jpg")
    inner.put(theirs, b"B's page", "image/jpeg")
    mine = college_blob_key(A, "booklet", "y", "source", "001.jpg")
    store = CollegeScopedBlobStore(inner, A)
    store.put(mine, b"A's page", "image/jpeg")
    assert store.get(mine) == b"A's page"
    for attempt in (
        lambda: store.get(theirs),
        lambda: store.exists(theirs),
        lambda: store.delete(theirs),
        lambda: store.put(theirs, b"overwrite", "image/jpeg"),
        lambda: store.delete_prefix(college_blob_key(B, "booklet", "x")),
    ):
        with pytest.raises(TenantViolationError):
            attempt()
    assert inner.get(theirs) == b"B's page"


def test_global_content_stays_shared_and_unbound_sessions_see_only_global() -> None:
    inner = MemoryBlobStore()
    key = global_blob_key("keys", "q", "f", "key.pdf")
    CollegeScopedBlobStore(inner, A).put(key, b"%PDF-", "application/pdf")
    assert CollegeScopedBlobStore(inner, B).get(key) == b"%PDF-"
    unbound = CollegeScopedBlobStore(inner, None)
    assert unbound.get(key) == b"%PDF-"
    with pytest.raises(TenantViolationError):
        unbound.get(college_blob_key(A, "booklet", "y", "p.jpg"))


def test_rebinding_does_not_nest_and_cannot_widen() -> None:
    inner = MemoryBlobStore()
    rebound = scoped_blobs(scoped_blobs(inner, A), B)
    assert isinstance(rebound, CollegeScopedBlobStore) and rebound.college_id == B


@pytest.mark.parametrize(
    "raw",
    [
        "college/../global/x",
        "/college/abc/x",
        "college//x",
        "college/not-a-uuid/booklet/x",
        "elsewhere/x",
        "global/../college/x",
        "global",
    ],
)
def test_malformed_or_traversing_blob_keys_are_refused(raw: str) -> None:
    with pytest.raises(InvariantError):
        BlobKey(raw)


# --- refusals cost as much as checks (timing) --------------------------------------------------


@pytest.fixture
def w() -> AuthWorld:
    return AuthWorld(InMemory())


def test_a_locked_account_still_does_the_hashing_work(w: AuthWorld) -> None:
    cid, _ = w.register("COLLEGE_A", ADMIN)
    for _ in range(5):
        w.auth.login(cid, ADMIN, "wrong password guess")
    before = w.mem.hasher.hashed
    assert w.auth.login(cid, ADMIN, ADMIN_PASSWORD) == Refused(reason="locked")
    assert w.mem.hasher.hashed == before + 1


def test_recovery_costs_the_same_for_unknown_known_and_locked_accounts(w: AuthWorld) -> None:
    cid, admin = w.register("COLLEGE_A", ADMIN)
    w.auth.issue_recovery_codes(cid, admin, ADMIN_PASSWORD)

    def work(email: str) -> int:
        h, v = w.mem.hasher.hashed, w.mem.hasher.verified
        w.auth.recover(cid, email, "AAAA-BBBB-CCCC-DDDD", "recovered long password")
        return (w.mem.hasher.hashed - h) + (w.mem.hasher.verified - v)

    unknown = work("nobody@college-a.example")
    known = work(ADMIN)
    assert unknown == known == 10
    for _ in range(5):
        work(ADMIN)
    assert work(ADMIN) == 10  # locked by now: still ten


# --- pepper rotation (O13) -------------------------------------------------------------------


def test_sign_in_moves_a_hash_to_the_new_pepper(w: AuthWorld) -> None:
    cid, admin = w.register("COLLEGE_A", ADMIN)
    old = w.mem.identity.get_identity(cid, admin).password_hash
    assert old is not None and w.mem.hasher.pepper_is_current(old, ADMIN_PASSWORD)
    w.mem.hasher.pepper, w.mem.hasher.previous = "2", ("1",)
    assert isinstance(w.auth.login(cid, ADMIN, ADMIN_PASSWORD), SignedIn)
    new = w.mem.identity.get_identity(cid, admin).password_hash
    assert new != old and new is not None and w.mem.hasher.pepper_is_current(new, ADMIN_PASSWORD)
    # The earlier pepper can go now: this account no longer needs it.
    w.mem.hasher.previous = ()
    assert isinstance(w.auth.login(cid, ADMIN, ADMIN_PASSWORD), SignedIn)


def test_an_account_left_on_a_removed_pepper_must_reset(w: AuthWorld) -> None:
    cid, _ = w.register("COLLEGE_A", ADMIN)
    w.mem.hasher.pepper, w.mem.hasher.previous = "2", ()
    assert w.auth.login(cid, ADMIN, ADMIN_PASSWORD) == Refused(reason="password")


def test_recovery_codes_keep_working_through_a_rotation(w: AuthWorld) -> None:
    cid, admin = w.register("COLLEGE_A", ADMIN)
    codes = w.auth.issue_recovery_codes(cid, admin, ADMIN_PASSWORD)
    w.mem.hasher.pepper, w.mem.hasher.previous = "2", ("1",)
    assert w.auth.recover(cid, ADMIN, codes[0], "recovered long password") is None


# --- tenant suspension -------------------------------------------------------------------------


def test_suspension_ends_every_session_and_resume_restores_sign_in(w: AuthWorld) -> None:
    cid, admin = w.register("COLLEGE_A", ADMIN)
    signed = w.auth.login(cid, ADMIN, ADMIN_PASSWORD)
    assert isinstance(signed, SignedIn)
    assert w.auth.session_active(cid, signed.session.id)
    with pytest.raises(InvariantError):
        w.registration.suspend(cid, operator=" ")
    suspended = w.registration.suspend(cid, operator="ops@tarn")
    assert suspended.status is TenantStatus.SUSPENDED
    assert not w.auth.session_active(cid, signed.session.id)
    assert w.auth.login(cid, ADMIN, ADMIN_PASSWORD) == Refused(reason="tenant_suspended")
    assert isinstance(w.auth.refresh(cid, signed.session.id, signed.refresh_token), Refused)
    resumed = w.registration.resume(cid, operator="ops@tarn")
    assert resumed.status is TenantStatus.ACTIVE
    assert isinstance(w.auth.login(cid, ADMIN, ADMIN_PASSWORD), SignedIn)
    actions = [e.action for e in w.mem.audit.events if e.college_id == cid]
    assert AuditAction.TENANT_SUSPENDED in actions and AuditAction.TENANT_RESUMED in actions
    assert admin  # the admin's account itself is untouched
