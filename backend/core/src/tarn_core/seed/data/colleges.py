"""The two demo colleges. Every name, email and USN is invented; none comes from samples/.

Institution IDs, emails and the development password are printed by ``make seed``. The
password is for local development only: ``tarn seed`` refuses to run when TARN_ENV is
production."""

from tarn_core.seed.data import aiml, assignments, constitution, ipr, papers
from tarn_core.seed.model import CollegeSeed, PersonSeed, StudentSeed

DEV_PASSWORD = "Tarn-Demo-Seed-2026!"  # noqa: S105  (development seed only)

ENGINEERING = CollegeSeed(
    institution_id="DEMO_ENG",
    name="Demo Engineering College (synthetic)",
    admin=PersonSeed("Asha Verma (demo admin)", "admin@demo-eng.example.test"),
    teachers=(
        PersonSeed("Ravi Menon (demo teacher)", "ravi.menon@demo-eng.example.test"),
        PersonSeed("Kavya Shetty (demo teacher)", "kavya.shetty@demo-eng.example.test"),
    ),
    students=tuple(
        StudentSeed(name, f"DEMOE{n:04d}", "CSE-5A" if n <= 6 else "CSE-5B")
        for n, name in enumerate(
            (
                "Aarav Test",
                "Bhavana Sample",
                "Chirag Demo",
                "Divya Example",
                "Eshan Fictional",
                "Farah Placeholder",
                "Gautam Invented",
                "Harini Mock",
                "Imran Trial",
                "Jaya Specimen",
                "Kiran Dummy",
                "Lakshmi Pretend",
            ),
            start=1,
        )
    ),
    subjects=(aiml.SUBJECT,),
    questions=tuple((aiml.SUBJECT.code, q) for q in aiml.QUESTIONS),
    papers=(),
)

COMMERCE = CollegeSeed(
    institution_id="DEMO_COM",
    name="Demo Commerce College (synthetic)",
    admin=PersonSeed("Meera Iyer (demo admin)", "admin@demo-com.example.test"),
    teachers=(
        PersonSeed("Suresh Naik (demo teacher)", "suresh.naik@demo-com.example.test"),
        PersonSeed("Pooja Rao (demo teacher)", "pooja.rao@demo-com.example.test"),
    ),
    students=tuple(
        StudentSeed(name, f"DEMOC{n:04d}", "BCOM-ACCA-2" if n <= 6 else "BCOM-CMA-3")
        for n, name in enumerate(
            (
                "Nikhil Test",
                "Omkar Sample",
                "Prerana Demo",
                "Qadir Example",
                "Rhea Fictional",
                "Sanjay Placeholder",
                "Tanvi Invented",
                "Uday Mock",
                "Vidya Trial",
                "Waseem Specimen",
                "Yamini Dummy",
                "Zoya Pretend",
            ),
            start=1,
        )
    ),
    subjects=(
        constitution.SUBJECT,
        ipr.SUBJECT,
        assignments.SUBJECT_EL,
        assignments.SUBJECT_SU,
    ),
    questions=(
        *((constitution.SUBJECT.code, q) for q in constitution.QUESTIONS),
        *((ipr.SUBJECT.code, q) for q in ipr.QUESTIONS),
        *((assignments.SUBJECT_EL.code, q) for q in assignments.QUESTIONS_EL),
        *((assignments.SUBJECT_SU.code, q) for q in assignments.QUESTIONS_SU),
    ),
    papers=papers.PAPERS_COMMERCE,
)

COLLEGES = (ENGINEERING, COMMERCE)
