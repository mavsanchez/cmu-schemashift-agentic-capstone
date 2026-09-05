# SchemaShift

## 1. What is SchemaShift?

**SchemaShift** is a local-first multi-agent data-engineering capstone that helps migrate SQL from an old database schema to a new schema.

The system uses a LangGraph parent **Context & Impact Agent** with specialized **Migration** and **Validation** subagents. Migration knowledge and durable agent memory are retrieved from persistent Redis storage, while PostgreSQL stores conversation, session, user/agent/tool history, and runtime events. Deterministic SQL/schema tools are exposed through MCP, DuckDB provides a safe local validation database, and Gradio provides the chat UI, live backend component visualization, file upload, activity trace, and human-review gate.

```text
Gradio
  ↓
LangGraph Context & Impact Agent
  ├── Redis memory + semantic retrieval
  ├── MCP → schema / SQL / comparison tools
  ├── Migration Subagent
  ├── Validation Subagent
  ├── Guardrail + Human Review
  └── Ollama / model provider
  ↓
PostgreSQL conversation + session + event history

Validation database: DuckDB
```

Every user turn is correlated using `conversation_id`, `session_id`, and `run_id`.

## 2. Setup and Run Locally

### Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/)
- Docker / Docker Compose
- Ollama

### Install

```bash
git clone <repository-url>
cd schemashift
uv sync
```

Pull the configured local model:

```bash
ollama pull <configured-model>
```

Start PostgreSQL and persistent Redis Stack:

```bash
docker compose up -d
```

Initialize local data and storage:

```bash
uv run python scripts/init_postgres.py
uv run python scripts/create_demo_data.py
uv run python scripts/ingest_knowledge.py
```

Run tests:

```bash
uv run pytest
```

Start SchemaShift:

```bash
uv run python -m schemashift.ui.app
```

Open the Gradio URL shown in the terminal, normally:

```text
http://127.0.0.1:7860
```
