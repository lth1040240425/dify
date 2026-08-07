# AGENTS.md

Dify is an open-source platform for building LLM applications, agentic workflows, and RAG pipelines. This monorepo contains the backend API (`api/`), frontend application (`web/`), deployment assets (`docker/`), standalone agent backend (`dify-agent/`), CLI (`cli/`), and end-to-end suite (`e2e/`). Follow the nearest scoped `AGENTS.md` for the files being changed.

## Repository Gotchas

- Run backend commands through `uv run --project api <command>`.
- Backend integration tests are CI-only and are not expected to run locally.

## Company Deployment References

Before working on company source builds, deployment, upgrades, dual-version coexistence, data migration, middleware cutover, rollback, Docker networking, or production incident diagnosis, read both documents in full:

- `docs/zh-CN/Dify-源码构建与持续升级方案.md`
- `docs/zh-CN/dify-双版本并行升级问题总结.md`
