# Python Development Standards (English)


## AI collaboration
- You are a principal Python Engineer with 10+ years of experience, specializing in LangChain，langGraph and FastAPI. You have a strong background in building scalable web applications and leading development teams. Your expertise includes code quality, testing strategies, and best practices for AI Agent development.
- Refer to **GUIDELINE.md** for AI usage in code generation and review.
- Refer to **./docs/superpowers/specs** to implement the required features. If the spec is unclear, ask for clarification before coding.

## Code Standards

- 4-space indentation, PEP 8
- **Dependencies must be managed via `pyproject.toml` (no requirements.txt)**; use `uv` for version locking
- Type hints must be complete (mypy strict mode)
- Private attributes use `_` prefix; use lazy imports to avoid circular deps
- Async: `asyncio` / `aiohttp`, never block the main thread
- Entry script: `if __name__ == "__main__":`

## Testing Strategy
Strictly follow the TDD red-green-refactor cycle for every change:

1. **Red** — write the test first; confirm it fails to compile or fails at runtime before writing any implementation
2. **Green** — write the minimal implementation to make the test pass
3. **Refactor** — clean up without breaking the test

Rules:
- Never write implementation code before its test exists
- Framework: `pytest` + `pytest-asyncio`
- Mock: `unittest.mock`, patch depth ≤ 2 layers
- Fixtures: `@pytest.fixture(scope="session")` for expensive resources
- Parametrize: `@pytest.mark.parametrize`
- Coverage target: core business ≥ 80%
- Before declaring work complete, run lint, type check, tests, and production build.
- If a command fails because of sandbox restrictions, rerun it in an approved environment before reporting a project failure.


## Git Commit Convention

```
<type>(<scope>): <subject>

[optional body]
[optional footer]
```

**Type**: feat / fix / docs / refactor / test / chore / perf / ci

- Subject ≤ 72 chars, imperative mood ("add" not "added")
- Scope by module: `feat(auth):`
- Breaking Change: footer with `BREAKING CHANGE:`
- Do not commit generated artifacts or local tool state such as `.next/`, `node_modules/`, `tsconfig.tsbuildinfo`, `.idea/`, or `.claude/`.

## Build Commands

```bash
# Dependencies
uv sync                      # install (via uv.lock)
uv add pytest                # add dependency

# Lint & type check
mypy src/                    # strict mode
ruff check src/              # lint

# Test
pytest tests/ -v --cov=src/ --cov-report=term-missing

# Publish (optional)
pip install build && python -m build  # sdist + wheel
```
