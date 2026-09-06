# SchemaShift benchmark result

- Mode: `live`
- Cases per arm: `50`
- Chat model: `gpt-oss:20b`
- Embedding model: `bge-m3`
- Temperature: `0.0`
- Seed: `20260905`
- Completed: `2026-09-06T02:21:03.635253+00:00`

| Arm | Equivalent | Schema-valid | Executed | Route accuracy | Retrieval recall | Reviews | Recovered | Model calls | Embeddings | Tool calls |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| baseline | 80.0% | 98.0% | 98.0% | 70.0% | 0.0% | 0 | 0 | 50 | 0 | 0 |
| workflow | 98.0% | 98.0% | 98.0% | 92.0% | 100.0% | 5 | 6 | 160 | 115 | 387 |

## Workflow minus baseline

- Equivalence: +18.0%
- Schema validity: +0.0%
- Execution: +0.0%
- Expected routing: +22.0%

Known result differences remain represented by `equivalent=false`; a human approval never changes that measurement.
