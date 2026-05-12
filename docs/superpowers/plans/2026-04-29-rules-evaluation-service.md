# Rules Evaluation Service Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a FastAPI service (`mountainash-rules-service`) that wraps the mountainash-utils-rules engine as an HTTP evaluation API with multi-ruleset support via slugged directory names.

**Architecture:** File-based ruleset registry loaded on startup. Each ruleset is a directory containing a YAML metadata file and a parquet/CSV rules file. A single `POST /evaluate/{ruleset_id}` endpoint accepts a context and returns matched rules. The service is stateless — rulesets are immutable after load.

**Tech Stack:** FastAPI, uvicorn, pydantic-settings, polars, PyYAML, mountainash-utils-rules, hatch, pytest, httpx

**Spec:** `docs/superpowers/specs/2026-04-29-rules-evaluation-service-design.md` (in mountainash-utils-rules repo)

**New repo location:** `/home/nathanielramm/git/mountainash-io/mountainash/mountainash-rules-service/`

---

## File Map

### Source files

| File | Responsibility |
|------|----------------|
| `src/mountainash_rules_service/__init__.py` | Package marker |
| `src/mountainash_rules_service/__version__.py` | Version string |
| `src/mountainash_rules_service/config.py` | `Settings` via pydantic-settings (rulesets_path, host, port, log_level) |
| `src/mountainash_rules_service/models.py` | Pydantic request/response models for all endpoints |
| `src/mountainash_rules_service/registry.py` | `RulesetRegistry`: loads rulesets from disk, holds engines, evaluates contexts |
| `src/mountainash_rules_service/routes.py` | FastAPI router with all endpoint handlers |
| `src/mountainash_rules_service/app.py` | FastAPI app factory with lifespan |

### Test files

| File | Responsibility |
|------|----------------|
| `tests/conftest.py` | Fixture rulesets, test registry, TestClient fixture |
| `tests/test_registry.py` | Registry loading, error handling, evaluate |
| `tests/test_routes.py` | HTTP-level endpoint tests |
| `tests/fixtures/sample-ruleset/ruleset.yaml` | Sample YAML for testing |
| `tests/fixtures/sample-ruleset/rules.parquet` | Sample rules data |
| `tests/fixtures/bad-yaml/ruleset.yaml` | Invalid YAML for error testing |
| `tests/fixtures/missing-rules/ruleset.yaml` | YAML pointing to nonexistent rules file |

### Config files

| File | Responsibility |
|------|----------------|
| `pyproject.toml` | Project metadata, dependencies, build config |
| `hatch.toml` | Hatch environments (default, test, ruff) |
| `pytest.ini` | Pytest config |
| `Dockerfile` | Container build |
| `CLAUDE.md` | Dev guidance |

---

## Task 1: Scaffold New Repo

**Files:**
- Create: `pyproject.toml`, `hatch.toml`, `pytest.ini`, `src/mountainash_rules_service/__init__.py`, `src/mountainash_rules_service/__version__.py`, `CLAUDE.md`

- [ ] **Step 1: Create repo directory and initialise git**

```bash
mkdir -p /home/nathanielramm/git/mountainash-io/mountainash/mountainash-rules-service
cd /home/nathanielramm/git/mountainash-io/mountainash/mountainash-rules-service
git init
```

- [ ] **Step 2: Create pyproject.toml**

Create `pyproject.toml`:

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "mountainash_rules_service"
dynamic = ["version"]
description = "Mountain Ash Rules Evaluation Service — FastAPI wrapper for mountainash-utils-rules"
readme = "README.md"
requires-python = ">=3.12"
keywords = ["rules-engine", "fastapi", "evaluation"]
authors = [
    { name = "Nathaniel Ramm", email = "nathaniel.ramm@discretedatascience.com" },
]
dependencies = [
    "fastapi>=0.115.0",
    "uvicorn[standard]>=0.30.0",
    "pydantic-settings>=2.0.0",
    "polars>=1.16.0",
    "pyyaml>=6.0",
    "mountainash-utils-rules",
    "mountainash-data",
    "mountainash",
]

[project.urls]
Documentation = "https://github.com/mountainash-io/mountainash-rules-service#readme"
Issues = "https://github.com/mountainash-io/mountainash-rules-service/issues"
Source = "https://github.com/mountainash-io/mountainash-rules-service"
```

- [ ] **Step 3: Create hatch.toml**

Create `hatch.toml`:

```toml
[metadata]
allow-direct-references = true

[version]
path = "src/mountainash_rules_service/__version__.py"

[build.targets.wheel]
packages = ["src/mountainash_rules_service"]

[envs.default]
installer = "uv"
path = ".venv"
dependencies = [
    "mountainash_rules @   {root:uri}/../mountainash-utils-rules",
    "mountainash_data @          {root:uri}/../mountainash-data",
    "mountainash @               {root:uri}/../mountainash",
    "mountainash_settings @      {root:uri}/../mountainash-settings",
]

[envs.default.scripts]
serve = "uvicorn mountainash_rules_service.app:app --reload --host 0.0.0.0 --port 8000"

[envs.test]
installer = "uv"
dependencies = [
    "pytest==8.3.5",
    "pytest-cov>=4.1.0",
    "httpx>=0.27.0",

    "mountainash_rules @   {root:uri}/../mountainash-utils-rules",
    "mountainash_data @          {root:uri}/../mountainash-data",
    "mountainash @               {root:uri}/../mountainash",
    "mountainash_settings @      {root:uri}/../mountainash-settings",
]

[envs.test.scripts]
test = "pytest -v"
test-cov = "pytest --cov --cov-report=term-missing -v"
test-target = "pytest {args} -v"

[envs.ruff]
installer = "uv"
dependencies = ["ruff==0.3.7"]

[envs.ruff.scripts]
check = "ruff check src/ tests/"
fix = "ruff check --fix src/ tests/"
```

- [ ] **Step 4: Create pytest.ini**

Create `pytest.ini`:

```ini
[pytest]
testpaths = tests
python_files = test_*.py
python_functions = test_*
addopts = -v -ra
```

- [ ] **Step 5: Create package init and version**

Create `src/mountainash_rules_service/__init__.py`:

```python
"""Mountain Ash Rules Evaluation Service."""
```

Create `src/mountainash_rules_service/__version__.py`:

```python
__version__ = "0.1.0"
```

- [ ] **Step 6: Create CLAUDE.md**

Create `CLAUDE.md`:

```markdown
# CLAUDE.md

## Project Overview

Mountain Ash Rules Evaluation Service — a FastAPI wrapper for the mountainash-utils-rules engine library. Loads rulesets from disk on startup and exposes them via HTTP endpoints for context-based rule evaluation.

## Build/Test/Lint Commands

- **Run dev server**: `hatch run serve`
- **Run tests**: `hatch run test:test`
- **Run single test**: `hatch run test:test-target tests/path/test.py::test_name`
- **Lint**: `hatch run ruff:check`

## Architecture

- `config.py` — pydantic-settings configuration
- `models.py` — request/response Pydantic models
- `registry.py` — RulesetRegistry: loads YAML + parquet/CSV, holds engines
- `routes.py` — FastAPI route handlers
- `app.py` — FastAPI app factory with lifespan

## Ruleset Format

Each ruleset is a directory under `rulesets/` containing:
- `ruleset.yaml` — metadata (name, description, dimensions with strategies)
- `rules.parquet` or `rules.csv` — rules data

The directory name is the ruleset slug used in API URLs.
```

- [ ] **Step 7: Initial commit**

```bash
git add -A
git commit -m "chore: scaffold mountainash-rules-service project"
```

---

## Task 2: Configuration

**Files:**
- Create: `src/mountainash_rules_service/config.py`
- Create: `tests/test_config.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_config.py`:

```python
"""Tests for service configuration."""

from pathlib import Path

from mountainash_rules_service.config import Settings


class TestSettings:
    def test_default_rulesets_path(self):
        settings = Settings()
        assert settings.rulesets_path == Path("./rulesets")

    def test_default_host(self):
        settings = Settings()
        assert settings.host == "0.0.0.0"

    def test_default_port(self):
        settings = Settings()
        assert settings.port == 8000

    def test_default_log_level(self):
        settings = Settings()
        assert settings.log_level == "info"

    def test_override_via_constructor(self):
        settings = Settings(rulesets_path=Path("/tmp/rules"), port=9000)
        assert settings.rulesets_path == Path("/tmp/rules")
        assert settings.port == 9000
```

- [ ] **Step 2: Run test to verify it fails**

Run: `hatch run test:test-target tests/test_config.py -v`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement config.py**

Create `src/mountainash_rules_service/config.py`:

```python
"""Service configuration via pydantic-settings."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Service settings with env var support."""

    rulesets_path: Path = Path("./rulesets")
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "info"

    model_config = {"env_prefix": ""}


_settings: Settings | None = None


def get_settings() -> Settings:
    """Return cached settings instance."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
```

- [ ] **Step 4: Run test to verify it passes**

Run: `hatch run test:test-target tests/test_config.py -v`
Expected: ALL PASS.

- [ ] **Step 5: Commit**

```bash
git add src/mountainash_rules_service/config.py tests/test_config.py
git commit -m "feat: add service configuration via pydantic-settings"
```

---

## Task 3: Pydantic Models

**Files:**
- Create: `src/mountainash_rules_service/models.py`
- Create: `tests/test_models.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_models.py`:

```python
"""Tests for request/response models."""

import pytest

from mountainash_rules_service.models import (
    EvaluateRequest,
    EvaluateResponse,
    RulesetSummary,
    RulesetDetail,
    RulesetListResponse,
    DimensionInfo,
)


class TestEvaluateRequest:
    def test_context_required(self):
        req = EvaluateRequest(context={"channel": "BROKER", "lvr": 75})
        assert req.context == {"channel": "BROKER", "lvr": 75}

    def test_empty_context_allowed(self):
        req = EvaluateRequest(context={})
        assert req.context == {}


class TestEvaluateResponse:
    def test_fields(self):
        resp = EvaluateResponse(
            ruleset_id="pricing-au",
            survivors=[{"rule_name": "r1", "__specificity": 2, "__rank": 1}],
            count=1,
            evaluated_dimensions=["channel"],
        )
        assert resp.ruleset_id == "pricing-au"
        assert resp.count == 1
        assert len(resp.survivors) == 1


class TestRulesetSummary:
    def test_fields(self):
        s = RulesetSummary(
            id="pricing-au",
            name="AU Pricing",
            description="Desc",
            dimensions=["channel", "lvr"],
            rule_count=10,
        )
        assert s.id == "pricing-au"
        assert s.rule_count == 10


class TestDimensionInfo:
    def test_exact_dimension(self):
        d = DimensionInfo(name="channel", strategy="EXACT", data_type="str")
        assert d.name == "channel"

    def test_range_dimension(self):
        d = DimensionInfo(
            name="lvr", strategy="RANGE", data_type="int",
            range_min_field="lvr_min", range_max_field="lvr_max",
        )
        assert d.range_min_field == "lvr_min"


class TestRulesetDetail:
    def test_fields(self):
        detail = RulesetDetail(
            id="pricing-au",
            name="AU Pricing",
            description="Desc",
            dimensions=[
                DimensionInfo(name="channel", strategy="EXACT", data_type="str"),
            ],
            rule_count=10,
        )
        assert len(detail.dimensions) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `hatch run test:test-target tests/test_models.py -v`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement models.py**

Create `src/mountainash_rules_service/models.py`:

```python
"""Pydantic request/response models for the rules evaluation API."""

from __future__ import annotations

import typing as t

from pydantic import BaseModel


class EvaluateRequest(BaseModel):
    """POST body for /evaluate/{ruleset_id}."""

    context: dict[str, t.Any]


class EvaluateResponse(BaseModel):
    """Response from /evaluate/{ruleset_id}."""

    ruleset_id: str
    survivors: list[dict[str, t.Any]]
    count: int
    evaluated_dimensions: list[str]


class DimensionInfo(BaseModel):
    """Dimension metadata in API responses."""

    name: str
    strategy: str
    data_type: str
    range_min_field: str | None = None
    range_max_field: str | None = None
    regex_pattern: str | None = None


class RulesetSummary(BaseModel):
    """Summary info for /rulesets list."""

    id: str
    name: str
    description: str
    dimensions: list[str]
    rule_count: int


class RulesetDetail(BaseModel):
    """Detailed info for /rulesets/{id}."""

    id: str
    name: str
    description: str
    dimensions: list[DimensionInfo]
    rule_count: int


class RulesetListResponse(BaseModel):
    """Response from GET /rulesets."""

    rulesets: list[RulesetSummary]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `hatch run test:test-target tests/test_models.py -v`
Expected: ALL PASS.

- [ ] **Step 5: Commit**

```bash
git add src/mountainash_rules_service/models.py tests/test_models.py
git commit -m "feat: add Pydantic request/response models"
```

---

## Task 4: Test Fixtures

**Files:**
- Create: `tests/fixtures/sample-ruleset/ruleset.yaml`
- Create: `tests/fixtures/sample-ruleset/rules.parquet` (generated by script)
- Create: `tests/fixtures/bad-yaml/ruleset.yaml`
- Create: `tests/fixtures/missing-rules/ruleset.yaml`
- Create: `tests/conftest.py`

- [ ] **Step 1: Create sample ruleset YAML**

Create `tests/fixtures/sample-ruleset/ruleset.yaml`:

```yaml
name: Sample Pricing Rules
description: Test ruleset for unit tests

rules_file: rules.parquet

dimensions:
  - name: region
    strategy: EXACT
    data_type: str
  - name: amount
    strategy: RANGE
    data_type: int
    range_min_field: amount_min
    range_max_field: amount_max
```

- [ ] **Step 2: Generate sample rules parquet**

Run this once to create the parquet fixture:

```bash
hatch run python -c "
import polars as pl
df = pl.DataFrame({
    'rule_name': ['specific', 'general', 'mid'],
    'region': ['AU', '<NA>', 'AU'],
    'amount_min': [0, -999999999, 0],
    'amount_max': [100, -999999999, 100],
    'margin': [-0.10, -0.01, -0.05],
})
df.write_parquet('tests/fixtures/sample-ruleset/rules.parquet')
print('Written', len(df), 'rules')
"
```

- [ ] **Step 3: Create bad YAML fixture**

Create `tests/fixtures/bad-yaml/ruleset.yaml`:

```yaml
this is: [not valid: yaml: content
  broken: true
```

- [ ] **Step 4: Create missing-rules fixture**

Create `tests/fixtures/missing-rules/ruleset.yaml`:

```yaml
name: Missing Rules File
description: Points to a file that does not exist

rules_file: nonexistent.parquet

dimensions:
  - name: region
    strategy: EXACT
    data_type: str
```

- [ ] **Step 5: Create conftest.py**

Create `tests/conftest.py`:

```python
"""Shared fixtures for rules service tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from mountainash_rules_service.registry import RulesetRegistry

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixtures_path() -> Path:
    return FIXTURES_DIR


@pytest.fixture
def sample_registry(fixtures_path) -> RulesetRegistry:
    return RulesetRegistry(fixtures_path)
```

- [ ] **Step 6: Commit**

```bash
git add tests/fixtures/ tests/conftest.py
git commit -m "test: add fixture rulesets and conftest"
```

---

## Task 5: RulesetRegistry

**Files:**
- Create: `src/mountainash_rules_service/registry.py`
- Create: `tests/test_registry.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_registry.py`:

```python
"""Tests for RulesetRegistry."""

from pathlib import Path

import pytest
from mountainash.relations import relation

from mountainash_rules_service.registry import RulesetRegistry

FIXTURES_DIR = Path(__file__).parent / "fixtures"


class TestRegistryLoading:
    def test_loads_sample_ruleset(self):
        registry = RulesetRegistry(FIXTURES_DIR)
        rulesets = registry.list_rulesets()
        ids = [r.id for r in rulesets]
        assert "sample-ruleset" in ids

    def test_skips_bad_yaml(self):
        registry = RulesetRegistry(FIXTURES_DIR)
        ids = [r.id for r in registry.list_rulesets()]
        assert "bad-yaml" not in ids

    def test_skips_missing_rules(self):
        registry = RulesetRegistry(FIXTURES_DIR)
        ids = [r.id for r in registry.list_rulesets()]
        assert "missing-rules" not in ids

    def test_sample_rule_count(self):
        registry = RulesetRegistry(FIXTURES_DIR)
        detail = registry.get_ruleset("sample-ruleset")
        assert detail.rule_count == 3

    def test_sample_dimensions(self):
        registry = RulesetRegistry(FIXTURES_DIR)
        detail = registry.get_ruleset("sample-ruleset")
        dim_names = [d.name for d in detail.dimensions]
        assert "region" in dim_names
        assert "amount" in dim_names

    def test_unknown_ruleset_raises(self):
        registry = RulesetRegistry(FIXTURES_DIR)
        with pytest.raises(KeyError):
            registry.get_ruleset("nonexistent")

    def test_empty_directory_raises(self, tmp_path):
        empty = tmp_path / "empty_rulesets"
        empty.mkdir()
        with pytest.raises(RuntimeError, match="No rulesets"):
            RulesetRegistry(empty)


class TestRegistryEvaluate:
    def test_evaluate_returns_survivors(self):
        registry = RulesetRegistry(FIXTURES_DIR)
        result = registry.evaluate(
            ruleset_id="sample-ruleset",
            context={"region": "AU", "amount": 50},
        )
        assert result.count >= 1

    def test_evaluate_specific_match(self):
        registry = RulesetRegistry(FIXTURES_DIR)
        result = registry.evaluate(
            ruleset_id="sample-ruleset",
            context={"region": "AU", "amount": 50},
        )
        names = [s["rule_name"] for s in result.survivors]
        assert "specific" in names

    def test_evaluate_top_n(self):
        registry = RulesetRegistry(FIXTURES_DIR)
        result = registry.evaluate(
            ruleset_id="sample-ruleset",
            context={"region": "AU", "amount": 50},
            top_n=1,
        )
        assert result.count == 1

    def test_evaluate_unknown_ruleset_raises(self):
        registry = RulesetRegistry(FIXTURES_DIR)
        with pytest.raises(KeyError):
            registry.evaluate(ruleset_id="nope", context={"region": "AU"})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `hatch run test:test-target tests/test_registry.py -v`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement registry.py**

Create `src/mountainash_rules_service/registry.py`:

```python
"""RulesetRegistry: loads rulesets from disk and holds engine instances."""

from __future__ import annotations

import logging
import typing as t
from pathlib import Path

import polars as pl
import yaml
from mountainash.relations import relation

from mountainash_rules.constants import MatchStrategy
from mountainash_rules.dimension import Dimension, DimensionsMetadata
from mountainash_rules.engine import ExpressionRulesEngine

from mountainash_rules_service.models import (
    DimensionInfo,
    EvaluateResponse,
    RulesetDetail,
    RulesetSummary,
)

logger = logging.getLogger(__name__)

_DATA_TYPE_MAP = {"str": str, "int": int, "float": float}
_STRATEGY_MAP = {s.name: s for s in MatchStrategy}

_INTERNAL_PREFIXES = ("__t_", "__ctx_")


class RulesetRegistry:
    """Loads rulesets from a directory and holds live engine instances."""

    def __init__(self, rulesets_path: Path) -> None:
        self._engines: dict[str, ExpressionRulesEngine] = {}
        self._info: dict[str, _RulesetMeta] = {}
        self._load_all(rulesets_path)
        if not self._engines:
            raise RuntimeError(
                f"No rulesets loaded from {rulesets_path}. "
                f"Check directory structure and ruleset.yaml files."
            )

    def __len__(self) -> int:
        return len(self._engines)

    def list_rulesets(self) -> list[RulesetSummary]:
        return [
            RulesetSummary(
                id=slug,
                name=meta.name,
                description=meta.description,
                dimensions=[d.dimension_name for d in meta.metadata.dimensions],
                rule_count=meta.rule_count,
            )
            for slug, meta in self._info.items()
        ]

    def get_ruleset(self, ruleset_id: str) -> RulesetDetail:
        if ruleset_id not in self._info:
            raise KeyError(f"Ruleset '{ruleset_id}' not found")
        meta = self._info[ruleset_id]
        return RulesetDetail(
            id=ruleset_id,
            name=meta.name,
            description=meta.description,
            dimensions=[
                DimensionInfo(
                    name=d.dimension_name,
                    strategy=d.match_strategy.name,
                    data_type=d.data_type.__name__,
                    range_min_field=d.range_min_field,
                    range_max_field=d.range_max_field,
                    regex_pattern=d.regex_pattern,
                )
                for d in meta.metadata.dimensions
            ],
            rule_count=meta.rule_count,
        )

    def evaluate(
        self,
        ruleset_id: str,
        context: dict[str, t.Any],
        top_n: int | None = None,
        min_specificity: int | None = None,
        dimensions: list[str] | None = None,
    ) -> EvaluateResponse:
        if ruleset_id not in self._engines:
            raise KeyError(f"Ruleset '{ruleset_id}' not found")

        engine = self._engines[ruleset_id]
        meta = self._info[ruleset_id]

        result = engine.evaluate(
            context=context,
            top_n=top_n,
            min_specificity=min_specificity,
            dimensions=dimensions,
        )

        rows = relation(result.survivors).to_dicts()
        cleaned = [
            {k: v for k, v in row.items() if not any(k.startswith(p) for p in _INTERNAL_PREFIXES)}
            for row in rows
        ]

        return EvaluateResponse(
            ruleset_id=ruleset_id,
            survivors=cleaned,
            count=result.count,
            evaluated_dimensions=result.active_dimensions,
        )

    def _load_all(self, rulesets_path: Path) -> None:
        if not rulesets_path.is_dir():
            logger.error(f"Rulesets path does not exist: {rulesets_path}")
            return

        for entry in sorted(rulesets_path.iterdir()):
            if not entry.is_dir():
                continue
            slug = entry.name
            try:
                self._load_one(slug, entry)
            except Exception:
                logger.exception(f"Failed to load ruleset '{slug}'")

    def _load_one(self, slug: str, path: Path) -> None:
        yaml_path = path / "ruleset.yaml"
        if not yaml_path.exists():
            logger.warning(f"Skipping '{slug}': no ruleset.yaml")
            return

        with open(yaml_path) as f:
            raw = yaml.safe_load(f)

        if not isinstance(raw, dict):
            logger.error(f"Skipping '{slug}': ruleset.yaml is not a mapping")
            return

        name = raw.get("name", slug)
        description = raw.get("description", "")
        rules_file = raw.get("rules_file", "rules.parquet")

        rules_path = path / rules_file
        if not rules_path.exists():
            logger.error(f"Skipping '{slug}': rules file not found: {rules_path}")
            return

        if rules_path.suffix == ".parquet":
            df = pl.read_parquet(rules_path)
        elif rules_path.suffix == ".csv":
            df = pl.read_csv(rules_path)
        else:
            logger.error(f"Skipping '{slug}': unsupported rules file format: {rules_path.suffix}")
            return

        dims = self._parse_dimensions(raw.get("dimensions", []))
        metadata = DimensionsMetadata(dimensions=dims)
        engine = ExpressionRulesEngine(rules=df, dimension_metadata=metadata)

        self._engines[slug] = engine
        self._info[slug] = _RulesetMeta(
            name=name,
            description=description,
            metadata=metadata,
            rule_count=len(df),
        )
        logger.info(f"Loaded ruleset '{slug}': {len(df)} rules, {len(dims)} dimensions")

    def _parse_dimensions(self, raw_dims: list[dict]) -> list[Dimension]:
        dims: list[Dimension] = []
        for raw in raw_dims:
            strategy = _STRATEGY_MAP[raw["strategy"]]
            data_type = _DATA_TYPE_MAP[raw["data_type"]]
            kwargs: dict[str, t.Any] = {
                "dimension_name": raw["name"],
                "match_strategy": strategy,
                "data_type": data_type,
            }
            if "context_field" in raw:
                kwargs["context_field"] = raw["context_field"]
            if "rule_field" in raw:
                kwargs["rule_field"] = raw["rule_field"]
            if "range_min_field" in raw:
                kwargs["range_min_field"] = raw["range_min_field"]
            if "range_max_field" in raw:
                kwargs["range_max_field"] = raw["range_max_field"]
            if "range_min_inclusive" in raw:
                kwargs["range_min_inclusive"] = raw["range_min_inclusive"]
            if "range_max_inclusive" in raw:
                kwargs["range_max_inclusive"] = raw["range_max_inclusive"]
            if "regex_pattern" in raw:
                kwargs["regex_pattern"] = raw["regex_pattern"]
            if "valid_values" in raw:
                kwargs["valid_values"] = raw["valid_values"]
            dims.append(Dimension(**kwargs))
        return dims


class _RulesetMeta:
    """Internal metadata holder for a loaded ruleset."""

    def __init__(
        self,
        name: str,
        description: str,
        metadata: DimensionsMetadata,
        rule_count: int,
    ) -> None:
        self.name = name
        self.description = description
        self.metadata = metadata
        self.rule_count = rule_count
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `hatch run test:test-target tests/test_registry.py -v`
Expected: ALL PASS.

- [ ] **Step 5: Commit**

```bash
git add src/mountainash_rules_service/registry.py tests/test_registry.py
git commit -m "feat: implement RulesetRegistry with file-based loading"
```

---

## Task 6: FastAPI App and Routes

**Files:**
- Create: `src/mountainash_rules_service/routes.py`
- Create: `src/mountainash_rules_service/app.py`
- Create: `tests/test_routes.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_routes.py`:

```python
"""HTTP-level tests for API routes."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mountainash_rules_service.app import create_app
from mountainash_rules_service.registry import RulesetRegistry

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def client():
    app = create_app(rulesets_path=FIXTURES_DIR)
    return TestClient(app)


class TestListRulesets:
    def test_returns_200(self, client):
        resp = client.get("/rulesets")
        assert resp.status_code == 200

    def test_contains_sample_ruleset(self, client):
        resp = client.get("/rulesets")
        ids = [r["id"] for r in resp.json()["rulesets"]]
        assert "sample-ruleset" in ids

    def test_sample_has_rule_count(self, client):
        resp = client.get("/rulesets")
        sample = [r for r in resp.json()["rulesets"] if r["id"] == "sample-ruleset"][0]
        assert sample["rule_count"] == 3


class TestGetRuleset:
    def test_returns_200(self, client):
        resp = client.get("/rulesets/sample-ruleset")
        assert resp.status_code == 200

    def test_returns_dimensions(self, client):
        resp = client.get("/rulesets/sample-ruleset")
        dims = resp.json()["dimensions"]
        names = [d["name"] for d in dims]
        assert "region" in names
        assert "amount" in names

    def test_unknown_returns_404(self, client):
        resp = client.get("/rulesets/nonexistent")
        assert resp.status_code == 404


class TestEvaluate:
    def test_returns_200(self, client):
        resp = client.post(
            "/evaluate/sample-ruleset",
            json={"context": {"region": "AU", "amount": 50}},
        )
        assert resp.status_code == 200

    def test_returns_survivors(self, client):
        resp = client.post(
            "/evaluate/sample-ruleset",
            json={"context": {"region": "AU", "amount": 50}},
        )
        data = resp.json()
        assert data["count"] >= 1
        assert "specific" in [s["rule_name"] for s in data["survivors"]]

    def test_top_n_param(self, client):
        resp = client.post(
            "/evaluate/sample-ruleset?top_n=1",
            json={"context": {"region": "AU", "amount": 50}},
        )
        assert resp.json()["count"] == 1

    def test_min_specificity_param(self, client):
        resp = client.post(
            "/evaluate/sample-ruleset?min_specificity=2",
            json={"context": {"region": "AU", "amount": 50}},
        )
        for s in resp.json()["survivors"]:
            assert s["__specificity"] >= 2

    def test_dimensions_param(self, client):
        resp = client.post(
            "/evaluate/sample-ruleset?dimensions=region",
            json={"context": {"region": "AU", "amount": 50}},
        )
        assert resp.json()["evaluated_dimensions"] == ["region"]

    def test_unknown_ruleset_returns_404(self, client):
        resp = client.post(
            "/evaluate/nonexistent",
            json={"context": {"region": "AU"}},
        )
        assert resp.status_code == 404

    def test_internal_columns_stripped(self, client):
        resp = client.post(
            "/evaluate/sample-ruleset",
            json={"context": {"region": "AU", "amount": 50}},
        )
        for s in resp.json()["survivors"]:
            for key in s:
                assert not key.startswith("__t_")
                assert not key.startswith("__ctx_")

    def test_specificity_and_rank_preserved(self, client):
        resp = client.post(
            "/evaluate/sample-ruleset",
            json={"context": {"region": "AU", "amount": 50}},
        )
        first = resp.json()["survivors"][0]
        assert "__specificity" in first
        assert "__rank" in first
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `hatch run test:test-target tests/test_routes.py -v`
Expected: FAIL — modules do not exist.

- [ ] **Step 3: Implement routes.py**

Create `src/mountainash_rules_service/routes.py`:

```python
"""FastAPI route handlers for the rules evaluation API."""

from __future__ import annotations

import typing as t

from fastapi import APIRouter, HTTPException, Query, Request

from mountainash_rules_service.models import (
    EvaluateRequest,
    EvaluateResponse,
    RulesetDetail,
    RulesetListResponse,
)

router = APIRouter()


def _get_registry(request: Request):
    return request.app.state.registry


@router.get("/rulesets", response_model=RulesetListResponse)
def list_rulesets(request: Request) -> RulesetListResponse:
    registry = _get_registry(request)
    return RulesetListResponse(rulesets=registry.list_rulesets())


@router.get("/rulesets/{ruleset_id}", response_model=RulesetDetail)
def get_ruleset(request: Request, ruleset_id: str) -> RulesetDetail:
    registry = _get_registry(request)
    try:
        return registry.get_ruleset(ruleset_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Ruleset '{ruleset_id}' not found")


@router.post("/evaluate/{ruleset_id}", response_model=EvaluateResponse)
def evaluate(
    request: Request,
    ruleset_id: str,
    body: EvaluateRequest,
    top_n: int | None = Query(None),
    min_specificity: int | None = Query(None),
    dimensions: str | None = Query(None),
) -> EvaluateResponse:
    registry = _get_registry(request)
    dim_list = dimensions.split(",") if dimensions else None
    try:
        return registry.evaluate(
            ruleset_id=ruleset_id,
            context=body.context,
            top_n=top_n,
            min_specificity=min_specificity,
            dimensions=dim_list,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Ruleset '{ruleset_id}' not found")
```

- [ ] **Step 4: Implement app.py**

Create `src/mountainash_rules_service/app.py`:

```python
"""FastAPI application factory."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from mountainash_rules_service.config import get_settings
from mountainash_rules_service.registry import RulesetRegistry
from mountainash_rules_service.routes import router

logger = logging.getLogger(__name__)


def create_app(rulesets_path: Path | None = None) -> FastAPI:
    """Create the FastAPI app.

    Args:
        rulesets_path: Override rulesets path (for testing).
            If None, reads from Settings.
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        path = rulesets_path or get_settings().rulesets_path
        app.state.registry = RulesetRegistry(path)
        logger.info(f"Loaded {len(app.state.registry)} rulesets from {path}")
        yield

    app = FastAPI(
        title="Mountain Ash Rules Service",
        description="Rules evaluation API",
        lifespan=lifespan,
    )
    app.include_router(router)
    return app


app = create_app()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `hatch run test:test-target tests/test_routes.py -v`
Expected: ALL PASS.

- [ ] **Step 6: Run full test suite**

Run: `hatch run test:test`
Expected: ALL PASS (config + models + registry + routes).

- [ ] **Step 7: Commit**

```bash
git add src/mountainash_rules_service/routes.py src/mountainash_rules_service/app.py tests/test_routes.py
git commit -m "feat: implement FastAPI routes and app factory"
```

---

## Task 7: Dockerfile and Smoke Test

**Files:**
- Create: `Dockerfile`
- Create: `rulesets/` example directory (gitignored, for local dev)

- [ ] **Step 1: Create Dockerfile**

Create `Dockerfile`:

```dockerfile
FROM python:3.12-slim AS base

WORKDIR /app

RUN pip install --no-cache-dir hatch

COPY pyproject.toml hatch.toml ./
COPY src/ src/

RUN hatch env create default

EXPOSE 8000

CMD ["hatch", "run", "uvicorn", "mountainash_rules_service.app:app", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 2: Create .gitignore**

Create `.gitignore`:

```
__pycache__/
*.pyc
.venv/
dist/
*.egg-info/
.pytest_cache/
.coverage
rulesets/
```

- [ ] **Step 3: Create example rulesets directory for local dev**

Create `rulesets/example/ruleset.yaml`:

```yaml
name: Example Rules
description: Example ruleset for local development

rules_file: rules.csv

dimensions:
  - name: category
    strategy: EXACT
    data_type: str
```

Create `rulesets/example/rules.csv`:

```csv
rule_name,category,score
high,A,100
medium,B,50
low,C,10
wildcard,<NA>,1
```

Note: The `rulesets/` directory is gitignored. These files are for local dev only — copy them manually or mount via Docker volume.

- [ ] **Step 4: Smoke test — start the server and hit it**

```bash
# Start the server (in background)
hatch run uvicorn mountainash_rules_service.app:app --host 0.0.0.0 --port 8000 &
sleep 2

# Hit the endpoints
curl -s http://localhost:8000/rulesets | python -m json.tool
curl -s http://localhost:8000/rulesets/example | python -m json.tool
curl -s -X POST http://localhost:8000/evaluate/example \
  -H "Content-Type: application/json" \
  -d '{"context": {"category": "A"}}' | python -m json.tool

# Kill the server
kill %1
```

Expected: JSON responses with the example ruleset listed, detail returned, and evaluation showing "high" and "wildcard" as survivors.

- [ ] **Step 5: Commit**

```bash
git add Dockerfile .gitignore
git commit -m "chore: add Dockerfile and gitignore"
```

---

## Task 8: Final Polish

**Files:**
- Modify: `src/mountainash_rules_service/__init__.py`

- [ ] **Step 1: Update __init__.py exports**

Update `src/mountainash_rules_service/__init__.py`:

```python
"""Mountain Ash Rules Evaluation Service."""

from mountainash_rules_service.__version__ import __version__
from mountainash_rules_service.app import create_app
from mountainash_rules_service.registry import RulesetRegistry

__all__ = ("__version__", "create_app", "RulesetRegistry")
```

- [ ] **Step 2: Run full test suite**

Run: `hatch run test:test`
Expected: ALL PASS.

- [ ] **Step 3: Run linter**

Run: `hatch run ruff:check`
Expected: Clean.

- [ ] **Step 4: Commit**

```bash
git add src/mountainash_rules_service/__init__.py
git commit -m "feat: finalize public API exports"
```
