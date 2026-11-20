Copied unchanged from NeMo RL `d51b2842` (`tests/functional/`). The image doesn't contain them: NeMo RL's
`.dockerignore` excludes `tests/functional/*/`, which Docker cleans to `tests/functional/*`, and CI runs its
tests from a mounted checkout instead. `smoke-entrypoint.sh` puts them back under `/opt/nemo-rl/tests/functional/`.
