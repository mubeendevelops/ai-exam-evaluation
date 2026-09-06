"""api/routers/ — HTTP endpoint modules.

auth.py, health.py, upload.py, evaluation.py, jobs.py, questions.py,
papers.py, exams.py, students.py. They are mounted by api/main.py, which
registers NO conditional routes: the app's route table is the same in every
environment.

Every router obtains its DB connection through api.deps.db.get_tenant_conn /
get_admin_conn as a FastAPI dependency. See api/deps/db.py for why no router
may ever open a raw connection itself. There is a third,
`get_auth_conn` — it sets NO RLS context and belongs to auth.py alone; read
its docstring before considering it anywhere else.

Every router requires an authenticated caller (api/deps/identity.py), except
health.py and the credential-granting half of auth.py.
"""
