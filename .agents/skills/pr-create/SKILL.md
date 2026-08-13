---
name: pr-create
description: Opens a pull request from the current branch against ai-dynamo/dynamo, including branch and remote checks, DCO verification, a repository-compliant Conventional Commit title, a complete PR body, push, and gh pr create. Use when a Dynamo change is committed on a branch and the user asks to open, create, submit, or draft the upstream pull request.
license: Apache-2.0
metadata:
  author: NVIDIA
  tags:
    - dynamo
    - github
    - pull-request
    - contribution
---

# Create an Upstream Pull Request

<!--
SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: CC-BY-4.0
-->

Open a pull request from the current branch to `ai-dynamo/dynamo`. Do not implement unrelated
changes or create an issue as part of this workflow.

## 1. Inspect the Branch

Run from the repository root:

```bash
git status --short --branch
git branch --show-current
git remote -v
git fetch upstream main
git log --oneline upstream/main..HEAD
git diff --stat upstream/main...HEAD
git diff --check upstream/main...HEAD
```

Stop and explain the problem when:

- the current branch is `main` or has no commits beyond `upstream/main`;
- tracked changes are uncommitted;
- the branch contains changes unrelated to the requested pull request; or
- `upstream` does not resolve to `ai-dynamo/dynamo`.

Ignore unrelated untracked files. Never add, commit, delete, or include them.

## 2. Verify Every Commit

Dynamo requires a Developer Certificate of Origin trailer on every commit. Check all branch commits:

```bash
git log --format='%h %s%n%(trailers:key=Signed-off-by)' upstream/main..HEAD
```

Each commit must contain a `Signed-off-by:` trailer matching its author identity. A cryptographic
GPG or SSH signature is optional and does not replace DCO sign-off. If sign-off is missing, stop and
show the appropriate repair command; do not rewrite published history without explicit approval.

Review the actual patch before drafting the pull request:

```bash
git diff upstream/main...HEAD
```

Use the validation results already produced for the branch. Do not claim a check was run unless its
result is known.

## 3. Draft the Title

Read the current rules in `AGENTS.md` and `.github/workflows/lint-pr-title.yaml`. Use:

```text
type(scope): imperative summary
```

Choose one allowed type from the workflow. Typical choices are `docs`, `fix`, `feat`, `test`,
`refactor`, `perf`, `ci`, `build`, or `chore`. Choose a short scope that names the affected area,
such as `skills`, `router`, `frontend`, `vllm`, or `operator`.

The title must describe the whole PR, not merely the last commit. Keep it concise, lowercase after
the colon, and omit a trailing period. Example:

```text
feat(skills): add upstream submission workflows
```

## 4. Draft the Body

Read `.github/pull_request_template.md` before drafting because it can change. At minimum, include
the repository-required sections:

```markdown
## Summary

- <what changed and why>

## Validation

- `<command>`

## Related Issues

- Closes #<issue>
```

When there is no related issue, use the template's explicit no-issue confirmation instead of
inventing one:

```markdown
## Related Issues

- [x] Confirmed — no related issue
```

Include useful details and a reviewer starting point when the current template requests them. Use
`Not run (<reason>)` for relevant checks that were not run. Do not include placeholder text.

Write the final body to a temporary file so quoting and Markdown remain intact.

## 5. Push and Open

Confirm GitHub authentication and determine the fork owner:

```bash
gh auth status
gh repo view --json nameWithOwner
gh repo view ai-dynamo/dynamo --json defaultBranchRef
```

Push the current branch to the contributor's fork without force:

```bash
git push -u origin "$(git branch --show-current)"
```

If an open pull request already exists for the branch, report it instead of creating a duplicate:

```bash
gh pr list --repo ai-dynamo/dynamo --head "<owner>:<branch>" --state open
```

Open the pull request explicitly against upstream `main`:

```bash
gh pr create \
  --repo ai-dynamo/dynamo \
  --base main \
  --head "<owner>:<branch>" \
  --title "<type(scope): summary>" \
  --body-file /tmp/dynamo-pr-body.md
```

Add `--draft` only when the user requested a draft or the change is intentionally not ready for
review. Leave maintainer edits enabled by default.

## 6. Report

Return the pull request number and URL, title, head/base branches, and validation performed. Remind
the user that full CI may require a maintainer comment of `/ok to test <short-sha>` when applicable.
