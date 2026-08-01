# Contributing to EyWALink

Thank you for your interest in contributing! EyWALink is built by agents, run by agents, for agents — but human contributors are always welcome too.

## Code of Conduct

This project adheres to our [Code of Conduct](CODE_OF_CONDUCT.md). By participating, you are expected to uphold this code.

## Development Setup

### Prerequisites

- Node.js 20+ (or use `.nvmrc`)
- pnpm 9+
- Python 3.12+ (for Python packages)
- uv (for Python dependency management)
- Docker + Docker Compose (for local services)

### Local Development

```bash
# Clone and install
git clone https://github.com/terrygzhou/EyWALink.git
cd EyWALink
pnpm install

# Verify setup
pnpm typecheck
pnpm test

# Run pre-commit hooks manually
pre-commit run --all-files
```

## Workflow

1. **Fork** the repository
2. **Create a branch** from `main` for your feature/fix
3. **Write tests** before or alongside implementation
4. **Submit a PR** with a clear description and reference any related issues

### Commit Convention

We follow [Conventional Commits](https://www.conventionalcommits.org/):

```
type(scope): description

- feat(core): add agent orchestration framework
- fix(deploy): handle Docker network timeout
- docs(readme): update quick start section
- chore(ci): add lint workflow
```

### Pull Request Process

- All PRs require at least one review before merging
- CI must pass (lint, typecheck, tests)
- Update documentation if your change affects public APIs
- Link related issues in the PR description

## Package Structure

This is a pnpm monorepo. Each package lives under `packages/`:

- `packages/core/` — Shared utilities and types
- `packages/deploy/` — Deployment infrastructure
- `packages/agents/` — AI agent frameworks
- `packages/observability/` — Monitoring and AIOps

When creating a new package:
1. Add to `pnpm-workspace.yaml` if needed
2. Create `package.json` with appropriate dependencies
3. Add to root `tsconfig.json` project references

## Tooling

| Tool | Purpose |
|------|---------|
| `pre-commit` | Pre-commit hooks (lint, format, security) |
| `eslint` | JavaScript/TypeScript linting |
| `ruff` | Python linting and formatting |
| `tsc` | TypeScript type checking |
| `vitest` | Unit testing |
| `GitHub Actions` | CI/CD pipelines |

## Reporting Issues

- Use the [GitHub Issues](https://github.com/terrygzhou/EyWALink/issues) template
- Include steps to reproduce, expected vs actual behavior
- For security vulnerabilities, email security@eywalink.org

## License

By contributing, you agree that your contributions will be licensed under the [MIT License](LICENSE).
