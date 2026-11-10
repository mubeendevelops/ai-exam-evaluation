"""Manage Question Papers on PostgreSQL (P34): ``pins_blueprint`` under row-level security and
the structure lock (D222) through the services as ``tarn_app``. The paging and filters run in
the shared list contract (``test_pg_schemas``)."""

import copy

import pytest

from tarn_adapters.postgres.testing import Opener, new_college
from tarn_core.errors import IllegalTransitionError
from tarn_core.services.blueprint_document import blueprint_to_document
from tarn_core.testing.builders import ci_shaped_blueprint, make_services, unit_for

pytestmark = pytest.mark.integration


def test_a_booklet_pins_the_paper_and_locks_its_structure(session: Opener) -> None:
    a = new_college(session, "PPA")
    b = new_college(session, "PPB")
    with session(a.id) as s:
        paper = ci_shaped_blueprint(s, a)
        assert not s.booklets.pins_blueprint(a.id, paper.id)
        make_services(s).booklets.register(
            a.id,
            a.teacher.id,
            student_id=a.students[0].id,
            unit_id=unit_for(s, a.id, paper.id),
            file_sha256="cd" * 32,
        )
    with session(a.id) as s:
        assert s.booklets.pins_blueprint(a.id, paper.id)
        svc = make_services(s).blueprints
        document = copy.deepcopy(blueprint_to_document(paper))
        document["course_code"] = "CIV-101"
        renamed = svc.update(a.id, a.teacher.id, paper.id, {**document, "title": "Renamed"})
        assert renamed.meta.version == 2 and svc.structure_locked(a.id, renamed)
        document["exam_code"] = "CIV-NEW"
        with pytest.raises(IllegalTransitionError, match="clone it"):
            svc.update(a.id, a.teacher.id, paper.id, document)
    with session(b.id) as s:
        # B's session asks about A's paper: row-level security shows no booklet.
        assert not s.booklets.pins_blueprint(a.id, paper.id)
        assert not s.booklets.pins_blueprint(b.id, paper.id)
