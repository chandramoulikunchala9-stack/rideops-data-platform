# Contributing to RideOps

## Branching

- `main` is always in a working state. CI must pass before anything is merged into it.
- Create a short-lived branch for each change, named `<type>/<short-description>`:
  - `feat/booking-generator`
  - `fix/silver-dedup-key`
  - `docs/aws-setup`
- Open a pull request into `main`, fill in the template, and merge after CI passes.

## Commit messages — Conventional Commits

Format: `<type>(<optional scope>): <summary in the imperative mood>`

| Type | Use for |
|---|---|
| `feat` | New functionality |
| `fix` | Bug fix |
| `docs` | Documentation only |
| `test` | Adding or fixing tests |
| `refactor` | Code change that neither fixes a bug nor adds a feature |
| `chore` | Tooling, dependencies, repository housekeeping |
| `ci` | GitHub Actions workflows |
| `infra` | Terraform or Databricks bundle changes |

Examples:

```
chore: initialize repository structure and project docs
feat(generators): add synthetic booking generator with seeded randomness
fix(silver): deduplicate bookings on booking_id keeping latest update
```

## Before you push

- Run the tests and linters for the area you changed (commands are listed in the README once each phase exists).
- Never commit `.env` files, credentials, Terraform state, or generated data under `data/`.
- Update `PROJECT_STATUS.md` when a milestone changes state.
