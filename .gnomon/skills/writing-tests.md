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

If this project declares test_must_fail_first under [verify], the harness
enforces the first paragraph mechanically: it restores the non-test files,
re-runs the check, and refuses a test that still passes. The instruction above
is what to do before there is a test to re-run.
