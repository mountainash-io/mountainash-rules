# Rules Evaluation Service — Design Spec

**Status:** Approved design. Ready for implementation planning.
**Date:** 2026-04-29
**Author:** Nathaniel Ramm (with Claude)
**Repo:** `mountainash-rules-service` (new repo, imports `mountainash-utils-rules` as a dependency)

---

## 1. Purpose

A FastAPI service that wraps `mountainash-utils-rules` as an HTTP evaluation API. Callers POST a context to `/evaluate/{ruleset_id}` and get back matched rules ranked by specificity. Multiple rulesets are loaded from disk on startup, each identified by a slug derived from its directory name.

This is the thinnest useful layer over the engine library — no database, no admin UI, no auth. It is the foundation for future MCP integration and SaaS management.

---

## 2. Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Engine type | Filter only (`ExpressionRulesEngine`) | Accumulator support added in a follow-up |
| Ruleset storage | File-based — parquet/CSV + YAML per directory | Stateless, container-friendly, no database needed |
| Response shape | All survivors by default, `top_n`/`min_specificity`/`dimensions` query params | Maps directly to engine `evaluate()` signature |
| Repo | New (`mountainash-rules-service`) | Library and service have different deployment lifecycles |

---

## 3. Ruleset Registry

### 3.1 Directory structure

A configurable root path (default `./rulesets/`) contains one directory per ruleset. The directory name is the `ruleset_id` slug:

```
rulesets/
├── pricing-au/
│   ├── ruleset.yaml
│   └── rules.parquet
├── fraud-detection/
│   ├── ruleset.yaml
│   └── rules.parquet
└── entity-pool/
    ├── ruleset.yaml
    └── rules.csv
```

### 3.2 Ruleset YAML schema

Each `ruleset.yaml` declares the metadata needed to construct an `ExpressionRulesEngine`:

```yaml
name: Australian Pricing Rules
description: Discretion margin rules for AU mortgage products

# Rules file relative to this directory (parquet preferred, CSV supported)
rules_file: rules.parquet

# Dimension definitions
dimensions:
  - name: channel
    strategy: EXACT
    data_type: str
  - name: lvr
    strategy: RANGE
    data_type: int
    range_min_field: lvr_min
    range_max_field: lvr_max
  - name: region
    strategy: EXACT
    data_type: str
```

Supported fields per dimension:
- `name` (required): dimension name, used as the context field and rule field unless overridden
- `strategy` (required): one of `EXACT`, `RANGE`, `GREATER_THAN`, `LESS_THAN`, `NOT_EQUAL`, `PREFIX`, `SUFFIX`, `CONTAINS`, `REGEX`, `SET_MEMBERSHIP`, `SET_EXCLUSION`
- `data_type` (required): `str`, `int`, or `float`
- `context_field` (optional): override the context key name
- `rule_field` (optional): override the rule column name
- `range_min_field` / `range_max_field` (required for RANGE): column names for min/max bounds
- `range_min_inclusive` / `range_max_inclusive` (optional, default `true`): bound inclusivity
- `regex_pattern` (required for REGEX): the pattern string
- `valid_values` (optional): list of allowed values

### 3.3 RulesetRegistry class

Loads all rulesets from disk on startup. Immutable after load. Held on `app.state` via FastAPI lifespan.

```python
class RulesetRegistry:
    def __init__(self, rulesets_path: Path):
        self._engines: dict[str, ExpressionRulesEngine] = {}
        self._info: dict[str, RulesetInfo] = {}
        self._load_all(rulesets_path)
```

**Loading logic:**
1. Scan `rulesets_path` for subdirectories
2. For each directory, read `ruleset.yaml`
3. Parse dimensions into `DimensionsMetadata`
4. Load rules file (parquet via `pl.read_parquet()`, CSV via `pl.read_csv()`)
5. Construct `ExpressionRulesEngine(rules=df, dimension_metadata=metadata)`
6. Store engine and metadata info keyed by directory name (the slug)

**Error handling on startup:**
- Missing `ruleset.yaml`: log warning, skip directory
- Invalid YAML or missing required fields: log error with details, skip directory
- Missing rules file: log error, skip directory
- Engine construction failure: log error with traceback, skip directory
- If zero rulesets load successfully: raise, fail startup

---

## 4. API Endpoints

### 4.1 `GET /rulesets` — list available rulesets

Response `200`:

```json
{
  "rulesets": [
    {
      "id": "pricing-au",
      "name": "Australian Pricing Rules",
      "description": "Discretion margin rules for AU mortgage products",
      "dimensions": ["channel", "lvr", "region"],
      "rule_count": 42
    }
  ]
}
```

### 4.2 `GET /rulesets/{ruleset_id}` — ruleset detail

Response `200`:

```json
{
  "id": "pricing-au",
  "name": "Australian Pricing Rules",
  "description": "Discretion margin rules for AU mortgage products",
  "dimensions": [
    {"name": "channel", "strategy": "EXACT", "data_type": "str"},
    {"name": "lvr", "strategy": "RANGE", "data_type": "int", "range_min_field": "lvr_min", "range_max_field": "lvr_max"},
    {"name": "region", "strategy": "EXACT", "data_type": "str"}
  ],
  "rule_count": 42
}
```

Response `404`: `{"detail": "Ruleset 'unknown-id' not found"}`

### 4.3 `POST /evaluate/{ruleset_id}` — evaluate a context

Request body:

```json
{
  "context": {
    "channel": "BROKER",
    "lvr": 75,
    "region": "AU"
  }
}
```

Query parameters (all optional):
- `top_n: int` — return only the top N matches by specificity
- `min_specificity: int` — minimum hard-match count to include
- `dimensions: str` — comma-separated subset of dimensions to evaluate

Response `200`:

```json
{
  "ruleset_id": "pricing-au",
  "survivors": [
    {
      "rule_name": "specific",
      "channel": "BROKER",
      "lvr_min": 60,
      "lvr_max": 80,
      "region": "AU",
      "margin": -0.10,
      "__specificity": 3,
      "__rank": 1
    }
  ],
  "count": 1,
  "evaluated_dimensions": ["channel", "lvr", "region"]
}
```

Response `404`: ruleset not found.
Response `422`: malformed context (FastAPI validation).

**Serialisation:** `RuleResult.survivors` is a backend DataFrame. The route handler converts it to a list of dicts via `relation(result.survivors).to_dicts()`. Internal columns (`__t_*`, `__ctx_*`) are stripped from the response. `__specificity` and `__rank` are preserved.

---

## 5. Application Structure

### 5.1 Project layout

```
mountainash-rules-service/
├── src/
│   └── mountainash_rules_service/
│       ├── __init__.py
│       ├── app.py              # FastAPI app factory, lifespan, CORS
│       ├── config.py           # Settings via pydantic-settings (rulesets_path, host, port)
│       ├── registry.py         # RulesetRegistry: load rulesets, hold engines, evaluate
│       ├── models.py           # Pydantic request/response models
│       └── routes.py           # Route handlers: /rulesets, /evaluate/{ruleset_id}
├── tests/
│   ├── conftest.py             # Test registry with fixture rulesets
│   ├── test_registry.py        # Loading, missing files, bad YAML, evaluate
│   ├── test_routes.py          # HTTP-level tests via httpx TestClient
│   └── fixtures/
│       └── sample-ruleset/
│           ├── ruleset.yaml
│           └── rules.parquet
├── pyproject.toml
├── hatch.toml
├── Dockerfile
├── CLAUDE.md
└── README.md
```

### 5.2 Dependencies

```toml
[project]
dependencies = [
    "fastapi>=0.115.0",
    "uvicorn[standard]>=0.30.0",
    "pydantic-settings>=2.0.0",
    "polars>=1.16.0",
    "pyyaml>=6.0",
    "mountainash-utils-rules",
]
```

### 5.3 Configuration

Via `pydantic-settings` with env var support:

```python
class Settings(BaseModel):
    rulesets_path: Path = Path("./rulesets")
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "info"
```

Env vars: `RULESETS_PATH`, `HOST`, `PORT`, `LOG_LEVEL`.

### 5.4 App factory and lifespan

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    app.state.registry = RulesetRegistry(settings.rulesets_path)
    logger.info(f"Loaded {len(app.state.registry)} rulesets")
    yield

app = FastAPI(
    title="Mountain Ash Rules Service",
    description="Rules evaluation API",
    lifespan=lifespan,
)
app.include_router(router)
```

### 5.5 Dockerfile

```dockerfile
FROM python:3.12-slim AS base
WORKDIR /app
COPY pyproject.toml hatch.toml ./
RUN pip install hatch && hatch env create default
COPY src/ src/
EXPOSE 8000
CMD ["hatch", "run", "uvicorn", "mountainash_rules_service.app:app", "--host", "0.0.0.0", "--port", "8000"]
```

Rulesets mounted at runtime: `docker run -v ./rulesets:/app/rulesets mountainash-rules-service`

---

## 6. Scope Exclusions

- **Accumulator engine support** — follow-up: add `engine: accumulator` to ruleset YAML, accumulator-specific response fields
- **MCP server** — follow-up: expose evaluate/list as MCP tools
- **Authentication / authorization** — no auth in v1
- **Rule authoring / management UI** — SaaS management layer is a separate sub-project
- **Database persistence** — file-based only
- **Hot-reload** — rulesets loaded on startup only; restart to pick up changes
- **Rate limiting / caching** — not needed for v1
- **Async evaluation** — the engine is synchronous; FastAPI wraps it in a threadpool executor by default for `def` routes, which is sufficient
