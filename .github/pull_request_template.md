## What this changes

<!-- One or two sentences. What is different after this pull request? -->

## Why

<!--
The bar for a change in this project is: does it make the tool more
honest, or more correct? If you are not sure, that is the question to
answer here.
-->

## Does this change a measurement?

<!--
This is the important one. If the change moves a reported number, say
so plainly and say what it was before and after. A change that moves
the reported speed by more than a few percent needs an explanation.

If it does not touch the measurement at all, say so - that is worth
knowing too.
-->

- [ ] No, the measurements are untouched.
- [ ] Yes, and here is what changed and why the new way is more correct:

## How was it checked?

<!--
What did you run? `pytest -q` and `ruff check .` are the floor. If you
touched a view, say what widths you looked at - the layout is checked
by the tests, but a look at the real thing catches more.
-->

- [ ] `pytest -q` passes
- [ ] `ruff check .` is clean
- [ ] I looked at the output at more than one terminal width
- [ ] I added a test that would have caught the bug (if this is a fix)

## Checklist

- [ ] The columns still line up, plain and coloured.
- [ ] The output still works without a TTY (`spdng speedtest | cat`).
- [ ] No new dependency was added, or the reason is in the description.
- [ ] Comments explain WHY, not WHAT.
