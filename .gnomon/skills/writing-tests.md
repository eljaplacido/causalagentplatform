+++
name = "writing a test that is worth having"
description = "Specify the behaviour before generating the test, and never encode a bug as the contract"
match = '\b(test|tests|unit test|coverage|spec|assert|pytest|vitest|regression)\b'
roles = ["implement", "implementor", "verifier", "critique"]
+++

A test is only worth having if it **fails on the code as it was and passes on
the code as it is**. A test written by reading the implementation passes by
construction and pins nothing -- and if the implementation is wrong, it pins the
bug as the contract, so the correct fix now breaks the suite.

Before writing the test, write down three things, in the test file:

1. **Preconditions.** What must be true for this to be called at all.
2. **Postconditions.** What the caller is entitled to afterwards. Take these
   from the docstring, the type signature, the issue, or the caller -- never
   from the body of the function under test.
3. **Undefined behaviour.** What the specification does not say. Write no
   assertions here; an assertion over undefined behaviour freezes an accident.

Then generate the test from 1-3, not from the implementation.

**When the implementation contradicts the specification, that is a finding, not
a detail to work around.** Say so plainly, and mark the test expected-to-fail
(xfail, test.fails, should_panic) rather than rewriting the expectation to match
the code. A test changed to agree with a bug is worse than no test: it is a bug
with a guard on it.

**Check that it bites.** Break the code deliberately and confirm the test goes
red. A test that passes against both the fixed and the broken version is
measuring nothing, however good it looks.

**A test that touches the database runs on two of them.** The backend suite
runs on SQLite and, in the gate's `backend-pytest-postgres` step, on the
PostgreSQL schema the migrations build. SQLite accepts a JSON string into a
`TEXT[]` column and an ISO string into a `TIMESTAMPTZ`; PostgreSQL does not, and
a test that seeds rows that way passes on one and fails on the other. Bind seed
rows as the app does, with `typed_text` and the types in `src/db/types.py`, and
read stored JSON and times back with `json_value` and `timestamp_text` rather
than comparing SQLite's text. A test that only means something on PostgreSQL
takes `postgres_only` from `tests/conftest.py`; one that is about SQLite itself
says so. Never skip a PostgreSQL failure to get green -- the step fails on skips.

If this project declares test_must_fail_first under [verify], the harness
enforces the first paragraph mechanically: it restores the non-test files,
re-runs the check, and refuses a test that still passes. The instruction above
is what to do before there is a test to re-run.
