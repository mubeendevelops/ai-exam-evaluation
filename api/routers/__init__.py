"""api/routers/ — HTTP endpoint modules. Empty for now; populated in later
sessions (upload, evaluate, results, ...).

Every router obtains its DB connection through api.deps.db.get_tenant_conn /
get_admin_conn as a FastAPI dependency. See api/deps/db.py for why no router
may ever open a raw connection itself.
"""
