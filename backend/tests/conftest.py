"""Create the schema in the test database before any tests run."""

from app.startup import run_startup

run_startup()
