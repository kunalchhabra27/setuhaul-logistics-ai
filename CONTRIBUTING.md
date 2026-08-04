# Contributing

## Branch workflow

Do not push feature work directly to `main`.

1. Pull the latest `main`.
2. Create a focused branch.
3. Make and test the change.
4. Push the branch and open a pull request.
5. Ask at least one teammate to review it.
6. Squash-merge after CI passes.

```bash
git checkout main
git pull origin main
git checkout -b feature/<short-name>

# work, then
git add .
git commit -m "feat: describe the change"
git push -u origin feature/<short-name>
```

Recommended branch prefixes:

- `feature/` new capability
- `fix/` bug fix
- `test/` tests
- `docs/` documentation
- `chore/` tooling or maintenance

## Commit style

Use concise conventional-style messages:

- `feat: add slot ranking policy`
- `fix: prevent booking without acceptance`
- `test: add simultaneous booking scenario`
- `docs: explain thread and exception identifiers`

## Pull-request rules

- Keep one logical change per PR.
- Explain the business rule being changed.
- Add or update tests for scheduler changes.
- Never let an LLM determine slot feasibility or booking.
- Never commit API keys, `.env` files, or local databases containing private data.
