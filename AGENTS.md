# AGENTS.md

## Configuration
- Don't add default values for config read from ENV variables unless explicitely requested

## Testing
- When executing tests always run a short lived container using the docker compose file
- Organize tests by the architectural piece they exercise, not by feature or contract. One test file per module/router/helper (e.g. `tests/test_lists.py` for `app/routers/lists.py`, `tests/test_utils.py` for `app/utils.py`). Place each test in the file that owns the code it calls — endpoint tests next to the router, helper tests next to the helper. Do not create per-feature files like `test_<feature>.py` that span multiple modules; if a contract cuts across components, split the tests into the relevant per-module files and use a section banner comment in each file to mark them as part of the same contract.

## Pull requests
- Keep PR descriptions to at most 4 lines (short paragraphs, no headings or bullet lists), plus the trailing attribution line. Cover what changed and why, anything a reviewer or deployer must know (new env vars, migrations, manual steps), and how it was verified. Leave the reasoning behind rejected alternatives out — put it in a code comment if it matters. The same limit applies to comments posted on a PR.
