"""api/ — FastAPI layer over the existing core/ library.

ARCHITECTURAL RULE (holds from this first file onward):
the API imports core/ directly. It never shells out to scripts/, and never
reimplements evaluation logic. scripts/ and api/ are two front doors onto the
same core/ functions; the moment one of them grows its own copy of the logic,
the two drift and the CLI stops being a usable debugging tool for the API.
"""
