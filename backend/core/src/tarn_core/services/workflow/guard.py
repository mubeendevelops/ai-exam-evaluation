"""The booklet lock and the version check every review write goes through (design.md
"Concurrency", D109).

One teacher holds a booklet open at a time. The lock is released when the teacher closes the
booklet, or lapses after ``LockPolicy.timeout`` without a write or a refresh; a lapsed lock is
taken over by the next teacher who opens the booklet. Every write also carries the version of
what it changes (the booklet's or the answer's), so a stale screen cannot overwrite a newer
change, even the same teacher's in another tab."""

from dataclasses import dataclass, replace
from datetime import timedelta

from tarn_core.domain.booklet import Booklet
from tarn_core.domain.review import BookletLock
from tarn_core.errors import BookletLockedError, InvariantError, StaleWriteError
from tarn_core.ids import BookletId, CollegeId, UserId
from tarn_core.ports.repositories import BookletRepository, UserRepository
from tarn_core.services._support import Runtime

DEFAULT_LOCK_MINUTES = 15


@dataclass(frozen=True, slots=True, kw_only=True)
class LockPolicy:
    timeout: timedelta = timedelta(minutes=DEFAULT_LOCK_MINUTES)

    def __post_init__(self) -> None:
        if self.timeout <= timedelta(0):
            raise InvariantError("the lock timeout must be positive")


@dataclass(frozen=True, slots=True, kw_only=True)
class Acquired:
    lock: BookletLock
    fresh: bool
    """The caller did not hold a live lock before (an open, not a refresh)."""
    taken_over: BookletLock | None
    """Another teacher's lapsed lock that this one replaced."""


class BookletGuard:
    def __init__(
        self,
        *,
        booklets: BookletRepository,
        users: UserRepository,
        runtime: Runtime,
        policy: LockPolicy | None = None,
    ) -> None:
        self._booklets = booklets
        self._users = users
        self._rt = runtime
        self._policy = policy or LockPolicy()

    def acquire(self, college_id: CollegeId, actor: UserId, booklet_id: BookletId) -> Acquired:
        """Take the lock, or refresh it when the caller holds it. Refused while another
        teacher's lock is live."""
        self._users.get(college_id, actor)
        current = self._booklets.lock(college_id, booklet_id)
        now = self._rt.clock.now()
        if current is not None and current.holder != actor and current.live(now):
            raise BookletLockedError(
                "another teacher has this booklet open",
                holder=current.holder,
                expires_at=current.expires_at,
            )
        mine = current is not None and current.holder == actor and current.live(now)
        lock = BookletLock(
            college_id=college_id,
            booklet_id=booklet_id,
            holder=actor,
            acquired_at=current.acquired_at if mine and current is not None else now,
            expires_at=now + self._policy.timeout,
        )
        self._booklets.save_lock(college_id, lock)
        taken_over = current if current is not None and current.holder != actor else None
        return Acquired(lock=lock, fresh=not mine, taken_over=taken_over)

    def release(self, college_id: CollegeId, actor: UserId, booklet_id: BookletId) -> bool:
        """Close the booklet. True when the caller held the lock (live or lapsed); someone
        else's lock is left alone."""
        current = self._booklets.lock(college_id, booklet_id)
        if current is None or current.holder != actor:
            return False
        self._booklets.delete_lock(college_id, booklet_id)
        return True

    def current(self, college_id: CollegeId, booklet_id: BookletId) -> BookletLock | None:
        """The live lock, if any."""
        lock = self._booklets.lock(college_id, booklet_id)
        return lock if lock is not None and lock.live(self._rt.clock.now()) else None

    def hold(
        self,
        college_id: CollegeId,
        actor: UserId,
        booklet_id: BookletId,
        *,
        expected_version: int | None = None,
    ) -> Booklet:
        """The booklet, for a write by ``actor``: they must hold its lock (their own lapsed
        lock is renewed if nobody took it over), and ``expected_version`` must be the stored
        version. The lock's expiry moves on."""
        self._users.get(college_id, actor)
        current = self._booklets.lock(college_id, booklet_id)
        now = self._rt.clock.now()
        if current is None or current.holder != actor:
            live = current is not None and current.live(now)
            raise BookletLockedError(
                "another teacher has this booklet open"
                if live
                else "open the booklet before changing it",
                holder=current.holder if live and current is not None else None,
                expires_at=current.expires_at if live and current is not None else None,
            )
        self._booklets.save_lock(
            college_id, replace(current, expires_at=now + self._policy.timeout)
        )
        booklet = self._booklets.get(college_id, booklet_id)
        if expected_version is not None and booklet.version != expected_version:
            raise StaleWriteError("the booklet changed since it was loaded: reload it")
        return booklet

    def ensure_free(self, college_id: CollegeId, actor: UserId, booklet_id: BookletId) -> None:
        """Refuse while another teacher's lock is live (before deleting the booklet)."""
        current = self.current(college_id, booklet_id)
        if current is not None and current.holder != actor:
            raise BookletLockedError(
                "another teacher has this booklet open",
                holder=current.holder,
                expires_at=current.expires_at,
            )
