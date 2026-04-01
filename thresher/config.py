"""Configuration loading with three-layer merge: defaults -> user YAML -> env vars."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from importlib.resources import files
from pathlib import Path
from typing import Any

import yaml

from thresher.types import ChunkerConfig, EmbeddingModelConfig, FileTypeGroup, RoutingRule
from thresher.url_resolver import UrlResolverConfig, parse_url_resolvers

logger = logging.getLogger("thresher.config")


@dataclass
class GCSConfig:
    bucket: str = ""
    source_prefix: str = ""
    expanded_prefix: str = "expanded/"
    cache_prefix: str = "cache/"
    queue_prefix: str = "queue/"


@dataclass
class SourceConfig:
    provider: str = "gcs"
    gcs: GCSConfig = field(default_factory=GCSConfig)


@dataclass
class QdrantConfig:
    url: str = "http://localhost:6333"
    api_key: str = ""
    timeout: int = 60
    batch_size: int = 100


@dataclass
class DestConfig:
    provider: str = "qdrant"
    qdrant: QdrantConfig = field(default_factory=QdrantConfig)


@dataclass
class RoutingConfig:
    default_collection: str = "default"
    rules: list[RoutingRule] = field(default_factory=list)


@dataclass
class QueueConfig:
    batch_size: int = 1000
    lease_timeout: int = 600
    max_reclaims: int = 1


@dataclass
class ProcessingConfig:
    docling_timeout: int = 600
    per_file_timeout: int = 600
    image_min_size: int = 51_200  # 50 KB
    max_pages: int = 500
    retry_max: int = 3
    memory_threshold_mb: int = 4096
    malloc_arena_max: int = 2
    archive_depth: int = 2
    summary_interval: int = 100
    archive_exclude_extensions: list[str] = field(
        default_factory=lambda: [".jar", ".war", ".whl", ".egg", ".apk", ".ipa"]
    )
    embed_batch_size: int = 256
    max_expansion_parallelism: int = 5
    upload_batch_size: int = 50
    expansion_timeout: int = 3600


@dataclass
class EmbeddingConfig:
    default: str = "default"
    models: dict[str, EmbeddingModelConfig] = field(default_factory=dict)


@dataclass
class K8sResourceSpec:
    cpu: str = ""
    memory: str = ""


@dataclass
class K8sResources:
    requests: K8sResourceSpec = field(
        default_factory=lambda: K8sResourceSpec(cpu="500m", memory="2Gi")
    )
    limits: K8sResourceSpec = field(default_factory=lambda: K8sResourceSpec(cpu="2", memory="4Gi"))


@dataclass
class K8sConfig:
    namespace: str = ""
    service_account: str = ""
    image: str = ""
    image_pull_policy: str = "IfNotPresent"
    runner_resources: K8sResources = field(default_factory=K8sResources)
    expander_resources: K8sResources = field(
        default_factory=lambda: K8sResources(
            requests=K8sResourceSpec(cpu="250m", memory="512Mi"),
            limits=K8sResourceSpec(cpu="1", memory="8Gi"),
        )
    )
    max_parallelism: int = 10
    node_selector: dict[str, str] = field(default_factory=dict)
    tolerations: list[dict[str, Any]] = field(default_factory=list)
    backoff_limit: int = 20
    ttl_seconds_after_finished: int = 3600
    config_configmap: str = ""
    credentials_secret: str = ""
    env_secrets: dict[str, dict[str, str]] = field(default_factory=dict)


@dataclass
class Config:
    source: SourceConfig = field(default_factory=SourceConfig)
    destination: DestConfig = field(default_factory=DestConfig)
    file_type_groups: dict[str, FileTypeGroup] = field(default_factory=dict)
    routing: RoutingConfig = field(default_factory=RoutingConfig)
    queue: QueueConfig = field(default_factory=QueueConfig)
    processing: ProcessingConfig = field(default_factory=ProcessingConfig)
    embedding: EmbeddingConfig = field(default_factory=EmbeddingConfig)
    kubernetes: K8sConfig = field(default_factory=K8sConfig)
    url_resolvers: list[UrlResolverConfig] = field(default_factory=list)
    force: bool = False


# Environment variable -> config path mapping
ENV_OVERRIDES: dict[str, str] = {
    "QDRANT_URL": "destination.qdrant.url",
    "QDRANT_API_KEY": "destination.qdrant.api_key",
    "GCS_BUCKET": "source.gcs.bucket",
}


def _load_defaults() -> dict[str, Any]:
    """Load built-in defaults.yaml from the package."""
    defaults_path = files("thresher") / "defaults.yaml"
    text = defaults_path.read_text(encoding="utf-8")  # type: ignore[union-attr]
    return yaml.safe_load(text)  # type: ignore[return-value]


def _deep_get(d: Any, dotted_path: str) -> Any:
    """Get a nested value by dotted path."""
    keys = dotted_path.split(".")
    current: Any = d
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _deep_set(d: dict[str, Any], dotted_path: str, value: str) -> None:
    """Set a nested value by dotted path, creating intermediate dicts."""
    keys = dotted_path.split(".")
    for key in keys[:-1]:
        d = d.setdefault(key, {})
    d[keys[-1]] = value


def _merge_configs(defaults: dict[str, Any], user: dict[str, Any]) -> dict[str, Any]:
    """Shallow merge: user top-level sections override defaults.

    file_type_groups: merge by group name — user-defined groups with the same
    name completely replace the built-in group (FR-026); unmentioned built-in
    groups are preserved.  The ``{**default_val, **user_val}`` dict merge
    achieves this because each group name maps to a complete group dict.

    Other sections: user keys override, default keys preserved.
    """
    merged: dict[str, Any] = {}
    all_keys = set(list(defaults.keys()) + list(user.keys()))

    for key in all_keys:
        default_val = defaults.get(key, {})
        user_val = user.get(key)

        if user_val is None:
            merged[key] = default_val
        elif (
            key == "file_type_groups"
            and isinstance(default_val, dict)
            and isinstance(user_val, dict)
        ):
            merged[key] = {**default_val, **user_val}
        elif isinstance(default_val, dict) and isinstance(user_val, dict):
            merged[key] = {**default_val, **user_val}
        else:
            merged[key] = user_val

    return merged


def _apply_env_overrides(config: dict[str, Any]) -> None:
    """Apply environment variable overrides."""
    for env_var, config_path in ENV_OVERRIDES.items():
        value = os.environ.get(env_var)
        if value is not None:
            _deep_set(config, config_path, value)


def _parse_file_type_groups(raw: dict[str, Any] | None) -> dict[str, FileTypeGroup]:
    """Parse raw file_type_groups dict into FileTypeGroup objects."""
    groups: dict[str, FileTypeGroup] = {}
    for name, spec in (raw or {}).items():
        if not isinstance(spec, dict):
            continue
        chunker_raw = spec.get("chunker", {})
        chunker = ChunkerConfig(
            strategy=chunker_raw.get("strategy", "chonkie-recursive"),
            chunk_size=chunker_raw.get("chunk_size", 512),
            language=chunker_raw.get("language", "auto"),
            recipe=chunker_raw.get("recipe", ""),
        )
        groups[name] = FileTypeGroup(
            name=name,
            extensions=spec.get("extensions", []),
            mime_types=spec.get("mime_types", []),
            detectors=spec.get("detectors", []),
            priority=spec.get("priority", 100),
            extractor=spec.get("extractor", "raw-text"),
            chunker=chunker,
            max_file_size=spec.get("max_file_size", 0),
        )
    return groups


def _parse_routing_rules(raw: list[Any] | None) -> list[RoutingRule]:
    """Parse raw routing rules list into RoutingRule objects."""
    rules: list[RoutingRule] = []
    for entry in raw or []:
        if not isinstance(entry, dict):
            continue
        rules.append(
            RoutingRule(
                collection=entry.get("collection", ""),
                name=entry.get("name", ""),
                file_group=entry.get("file_group", []),
                path=entry.get("path", []),
                filename=entry.get("filename", []),
                embedding=entry.get("embedding", ""),
                skip=bool(entry.get("skip", False)),
            )
        )
    return rules


def _parse_embedding_config(embed_raw: Any) -> "EmbeddingConfig":
    """Parse raw embedding config dict into EmbeddingConfig.

    When ``embedding.models`` is absent or empty, a ``"default"`` model entry
    is created using the built-in defaults for the all-MiniLM-L6-v2 model.
    """
    if not isinstance(embed_raw, dict):
        embed_raw = {}

    raw_models = embed_raw.get("models")
    if raw_models and isinstance(raw_models, dict):
        models: dict[str, EmbeddingModelConfig] = {}
        for name, spec in raw_models.items():
            if not isinstance(spec, dict):
                continue
            models[name] = EmbeddingModelConfig(
                model=str(spec.get("model", "")),
                vector_size=int(spec.get("vector_size", 384)),
                vector_name=str(spec.get("vector_name", "")),
                max_tokens=int(spec.get("max_tokens", 512)),
                index_prefix=str(spec.get("index_prefix", "")),
                query_prefix=str(spec.get("query_prefix", "")),
            )
        default_name = str(embed_raw.get("default", "default"))
    else:
        models = {
            "default": EmbeddingModelConfig(
                model="sentence-transformers/all-MiniLM-L6-v2",
                vector_size=384,
                vector_name="fast-all-minilm-l6-v2",
                max_tokens=512,
            )
        }
        default_name = "default"

    return EmbeddingConfig(
        default=default_name,
        models=models,
    )


def _build_config(raw: dict[str, Any]) -> Config:
    """Build a Config dataclass from a raw merged dict."""
    source_raw = raw.get("source", {})
    gcs_raw = source_raw.get("gcs", {}) if isinstance(source_raw, dict) else {}
    dest_raw = raw.get("destination", {})
    qdrant_raw = dest_raw.get("qdrant", {}) if isinstance(dest_raw, dict) else {}
    routing_raw = raw.get("routing", {})
    queue_raw = raw.get("queue", {})
    proc_raw = raw.get("processing", {})
    embed_raw = raw.get("embedding", {})
    k8s_raw = raw.get("kubernetes", {})

    gcs = GCSConfig(
        bucket=str(gcs_raw.get("bucket", "")),
        source_prefix=str(gcs_raw.get("source_prefix", "")),
        expanded_prefix=str(gcs_raw.get("expanded_prefix", "expanded/")),
        cache_prefix=str(gcs_raw.get("cache_prefix", "cache/")),
        queue_prefix=str(gcs_raw.get("queue_prefix", "queue/")),
    )

    qdrant = QdrantConfig(
        url=str(qdrant_raw.get("url", "http://localhost:6333")),
        api_key=str(qdrant_raw.get("api_key", "")),
        timeout=int(qdrant_raw.get("timeout", 60)),
        batch_size=int(qdrant_raw.get("batch_size", 100)),
    )

    file_type_groups = _parse_file_type_groups(raw.get("file_type_groups"))
    routing_rules = _parse_routing_rules(
        routing_raw.get("rules") if isinstance(routing_raw, dict) else None
    )

    # K8s resources
    k8s_resources_raw = k8s_raw.get("runner_resources", {}) if isinstance(k8s_raw, dict) else {}
    k8s_req = k8s_resources_raw.get("requests", {}) if isinstance(k8s_resources_raw, dict) else {}
    k8s_lim = k8s_resources_raw.get("limits", {}) if isinstance(k8s_resources_raw, dict) else {}

    exp_resources_raw = k8s_raw.get("expander_resources", {}) if isinstance(k8s_raw, dict) else {}
    exp_req = exp_resources_raw.get("requests", {}) if isinstance(exp_resources_raw, dict) else {}
    exp_lim = exp_resources_raw.get("limits", {}) if isinstance(exp_resources_raw, dict) else {}

    return Config(
        source=SourceConfig(
            provider=source_raw.get("provider", "gcs") if isinstance(source_raw, dict) else "gcs",
            gcs=gcs,
        ),
        destination=DestConfig(
            provider=(
                dest_raw.get("provider", "qdrant") if isinstance(dest_raw, dict) else "qdrant"
            ),
            qdrant=qdrant,
        ),
        file_type_groups=file_type_groups,
        routing=RoutingConfig(
            default_collection=str(
                routing_raw.get("default_collection", "default")
                if isinstance(routing_raw, dict)
                else "default"
            ),
            rules=routing_rules,
        ),
        queue=QueueConfig(
            batch_size=int(
                queue_raw.get("batch_size", 1000) if isinstance(queue_raw, dict) else 1000
            ),
            lease_timeout=int(
                queue_raw.get("lease_timeout", 600) if isinstance(queue_raw, dict) else 600
            ),
            max_reclaims=int(
                queue_raw.get("max_reclaims", 1) if isinstance(queue_raw, dict) else 1
            ),
        ),
        processing=ProcessingConfig(
            docling_timeout=int(
                proc_raw.get("docling_timeout", 600) if isinstance(proc_raw, dict) else 600
            ),
            per_file_timeout=int(
                proc_raw.get("per_file_timeout", 600) if isinstance(proc_raw, dict) else 600
            ),
            image_min_size=int(
                proc_raw.get("image_min_size", 51_200) if isinstance(proc_raw, dict) else 51_200
            ),
            max_pages=int(proc_raw.get("max_pages", 500) if isinstance(proc_raw, dict) else 500),
            retry_max=int(proc_raw.get("retry_max", 3) if isinstance(proc_raw, dict) else 3),
            memory_threshold_mb=int(
                proc_raw.get("memory_threshold_mb", 4096) if isinstance(proc_raw, dict) else 4096
            ),
            malloc_arena_max=int(
                proc_raw.get("malloc_arena_max", 2) if isinstance(proc_raw, dict) else 2
            ),
            archive_depth=int(
                proc_raw.get("archive_depth", 2) if isinstance(proc_raw, dict) else 2
            ),
            archive_exclude_extensions=(
                proc_raw.get(
                    "archive_exclude_extensions", [".jar", ".war", ".whl", ".egg", ".apk", ".ipa"]
                )
                if isinstance(proc_raw, dict)
                else [".jar", ".war", ".whl", ".egg", ".apk", ".ipa"]
            ),
            summary_interval=int(
                proc_raw.get("summary_interval", 100) if isinstance(proc_raw, dict) else 100
            ),
            embed_batch_size=int(
                proc_raw.get("embed_batch_size", 256) if isinstance(proc_raw, dict) else 256
            ),
            max_expansion_parallelism=int(
                proc_raw.get("max_expansion_parallelism", 5) if isinstance(proc_raw, dict) else 5
            ),
            upload_batch_size=int(
                proc_raw.get("upload_batch_size", 50) if isinstance(proc_raw, dict) else 50
            ),
            expansion_timeout=int(
                proc_raw.get("expansion_timeout", 3600) if isinstance(proc_raw, dict) else 3600
            ),
        ),
        embedding=_parse_embedding_config(embed_raw),
        kubernetes=K8sConfig(
            namespace=str(k8s_raw.get("namespace", "") if isinstance(k8s_raw, dict) else ""),
            service_account=str(
                k8s_raw.get("service_account", "") if isinstance(k8s_raw, dict) else ""
            ),
            image=str(k8s_raw.get("image", "") if isinstance(k8s_raw, dict) else ""),
            image_pull_policy=str(
                k8s_raw.get("image_pull_policy", "IfNotPresent")
                if isinstance(k8s_raw, dict)
                else "IfNotPresent"
            ),
            runner_resources=K8sResources(
                requests=K8sResourceSpec(
                    cpu=str(k8s_req.get("cpu", "500m") if isinstance(k8s_req, dict) else "500m"),
                    memory=str(
                        k8s_req.get("memory", "2Gi") if isinstance(k8s_req, dict) else "2Gi"
                    ),
                ),
                limits=K8sResourceSpec(
                    cpu=str(k8s_lim.get("cpu", "2") if isinstance(k8s_lim, dict) else "2"),
                    memory=str(
                        k8s_lim.get("memory", "4Gi") if isinstance(k8s_lim, dict) else "4Gi"
                    ),
                ),
            ),
            expander_resources=K8sResources(
                requests=K8sResourceSpec(
                    cpu=str(exp_req.get("cpu", "250m") if isinstance(exp_req, dict) else "250m"),
                    memory=str(
                        exp_req.get("memory", "512Mi") if isinstance(exp_req, dict) else "512Mi"
                    ),
                ),
                limits=K8sResourceSpec(
                    cpu=str(exp_lim.get("cpu", "1") if isinstance(exp_lim, dict) else "1"),
                    memory=str(
                        exp_lim.get("memory", "8Gi") if isinstance(exp_lim, dict) else "8Gi"
                    ),
                ),
            ),
            max_parallelism=int(
                k8s_raw.get("max_parallelism", 10) if isinstance(k8s_raw, dict) else 10
            ),
            node_selector=k8s_raw.get("node_selector", {}) if isinstance(k8s_raw, dict) else {},
            tolerations=k8s_raw.get("tolerations", []) if isinstance(k8s_raw, dict) else [],
            backoff_limit=int(
                k8s_raw.get("backoff_limit", 20) if isinstance(k8s_raw, dict) else 20
            ),
            ttl_seconds_after_finished=int(
                k8s_raw.get("ttl_seconds_after_finished", 3600)
                if isinstance(k8s_raw, dict)
                else 3600
            ),
            config_configmap=str(
                k8s_raw.get("config_configmap", "") if isinstance(k8s_raw, dict) else ""
            ),
            credentials_secret=str(
                k8s_raw.get("credentials_secret", "") if isinstance(k8s_raw, dict) else ""
            ),
            env_secrets=(k8s_raw.get("env_secrets", {}) if isinstance(k8s_raw, dict) else {}),
        ),
        url_resolvers=parse_url_resolvers(raw.get("url_resolvers")),
    )


def validate_config(merged: dict) -> list[str]:
    """Validate a merged config dict against the JSON Schema and semantic rules.

    Returns a list of validation error messages (empty if valid).
    """
    errors: list[str] = []

    try:
        import jsonschema
    except ImportError:
        logger.debug("jsonschema not installed — skipping config validation")
        return errors

    schema_path = Path(__file__).parent / "config_schema.json"
    if not schema_path.exists():
        logger.debug("Config schema not found at %s", schema_path)
        return errors

    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema)
    schema_errors = sorted(validator.iter_errors(merged), key=lambda e: list(e.absolute_path))
    errors.extend(
        [
            f"{'.'.join(str(p) for p in e.absolute_path) or '<root>'}: {e.message}"
            for e in schema_errors
        ]
    )

    # Semantic validation: embedding model references
    embed_raw = merged.get("embedding", {})
    if isinstance(embed_raw, dict):
        raw_models = embed_raw.get("models")
        if raw_models and isinstance(raw_models, dict):
            model_keys = set(raw_models.keys())
            default_name = embed_raw.get("default", "default")
            if default_name not in model_keys:
                errors.append(
                    f"embedding.default: '{default_name}' does not reference a key in "
                    "embedding.models"
                )
            # Check routing rules reference valid embedding model names
            routing_raw = merged.get("routing", {})
            if isinstance(routing_raw, dict):
                for i, rule in enumerate(routing_raw.get("rules", []) or []):
                    rule_embedding = rule.get("embedding", "")
                    if rule_embedding and rule_embedding not in model_keys:
                        errors.append(
                            f"routing.rules[{i}].embedding: '{rule_embedding}' does not reference "
                            f"a key in embedding.models"
                        )

    return errors


def load_config(config_path: str | Path | None = None) -> Config:
    """Load configuration with three-layer merge.

    1. Built-in defaults (thresher/defaults.yaml)
    2. User YAML config (if provided)
    3. Environment variable overrides
    """
    # Layer 1: built-in defaults
    defaults = _load_defaults()

    # Layer 2: user config
    user_config: dict[str, Any] = {}
    if config_path:
        path = Path(config_path)
        if path.exists():
            user_config = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    # Merge
    merged = _merge_configs(defaults, user_config)

    # Layer 3: env overrides
    _apply_env_overrides(merged)

    # Validate against schema
    errors = validate_config(merged)
    for err in errors:
        logger.warning("Config validation: %s", err)

    # Build and return
    return _build_config(merged)
