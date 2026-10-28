"""The one check every write to a unit's booklets goes through (P28, D164): once the SME has
submitted the unit to the admin, or the Governor has frozen it, nothing in it changes. Writes
are refused with ``UnitClosedError`` (HTTP 409); reads stay open."""

from tarn_core.domain.unit import Unit, UnitState
from tarn_core.errors import UnitClosedError
from tarn_core.ids import BookletId, CollegeId, UnitId
from tarn_core.ports.repositories import BookletRepository, UnitRepository

SUBMITTED = (
    "This unit has been submitted to your admin: nothing in it can be changed "
    "unless the admin returns it."
)
FROZEN = "This unit has been frozen and signed off: nothing in it can be changed."


def closed_message(unit: Unit) -> str:
    return FROZEN if unit.state is UnitState.FROZEN else SUBMITTED


def ensure_unit_open(units: UnitRepository, college_id: CollegeId, unit_id: UnitId) -> Unit:
    unit = units.get(college_id, unit_id)
    if not unit.open:
        raise UnitClosedError(closed_message(unit))
    return unit


def ensure_booklet_open(
    booklets: BookletRepository, units: UnitRepository, college_id: CollegeId, booklet_id: BookletId
) -> Unit:
    """The booklet's unit, if it is open."""
    booklet = booklets.get(college_id, booklet_id)
    return ensure_unit_open(units, college_id, booklet.unit_id)


__all__ = ["FROZEN", "SUBMITTED", "closed_message", "ensure_booklet_open", "ensure_unit_open"]
