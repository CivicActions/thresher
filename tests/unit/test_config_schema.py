"""Tests for config JSON Schema validation."""

from __future__ import annotations

from pathlib import Path

import yaml

from thresher.config import validate_config


class TestSchemaValidatesDefaults:
    """Built-in defaults.yaml must pass schema validation."""

    def test_defaults_yaml_valid(self):
        defaults_path = Path(__file__).parents[2] / "thresher" / "defaults.yaml"
        data = yaml.safe_load(defaults_path.read_text(encoding="utf-8"))
        errors = validate_config(data)
        assert errors == [], f"defaults.yaml has schema errors: {errors}"

    def test_example_config_valid(self):
        example_path = Path(__file__).parents[2] / "config.example.yaml"
        data = yaml.safe_load(example_path.read_text(encoding="utf-8"))
        assert data is not None
        errors = validate_config(data)
        assert errors == [], f"config.example.yaml has schema errors: {errors}"


class TestSchemaRejectsInvalid:
    """Invalid configs must produce validation errors."""

    def test_unknown_top_level_key(self):
        errors = validate_config({"bogus_key": "value"})
        assert len(errors) == 1
        assert "bogus_key" in errors[0]

    def test_wrong_type_integer(self):
        errors = validate_config({"processing": {"retry_max": "not-a-number"}})
        assert any("retry_max" in e for e in errors)

    def test_wrong_type_string(self):
        errors = validate_config({"source": {"provider": 123}})
        assert any("provider" in e for e in errors)

    def test_nested_unknown_key(self):
        errors = validate_config({"source": {"gcs": {"unknown_option": "x"}}})
        assert any("unknown_option" in e for e in errors)

    def test_invalid_enum_value(self):
        errors = validate_config({"source": {"provider": "s3"}})
        assert any("provider" in e for e in errors)

    def test_negative_integer(self):
        errors = validate_config({"processing": {"retry_max": -1}})
        assert any("retry_max" in e for e in errors)

    def test_invalid_extension_format(self):
        errors = validate_config(
            {
                "file_type_groups": {
                    "bad-group": {
                        "extensions": ["no_dot"],
                        "chunker": {"strategy": "skip"},
                    }
                }
            }
        )
        assert any("no_dot" in e or "extensions" in e for e in errors)

    def test_routing_rule_missing_collection(self):
        errors = validate_config({"routing": {"rules": [{"name": "no-collection-rule"}]}})
        assert any("collection" in e for e in errors)

    def test_url_resolver_missing_type(self):
        errors = validate_config({"url_resolvers": [{"match": "^foo"}]})
        assert any("type" in e for e in errors)

    def test_url_resolver_invalid_type(self):
        errors = validate_config({"url_resolvers": [{"type": "invalid-resolver"}]})
        assert any("type" in e for e in errors)

    def test_invalid_image_pull_policy(self):
        errors = validate_config({"kubernetes": {"image_pull_policy": "Sometimes"}})
        assert any("image_pull_policy" in e for e in errors)


class TestSchemaAcceptsValid:
    """Valid partial configs must pass validation."""

    def test_empty_config(self):
        errors = validate_config({})
        assert errors == []

    def test_source_only(self):
        errors = validate_config({"source": {"provider": "gcs"}})
        assert errors == []

    def test_processing_only(self):
        errors = validate_config({"processing": {"retry_max": 5, "archive_depth": 3}})
        assert errors == []

    def test_routing_with_rules(self):
        errors = validate_config(
            {
                "routing": {
                    "default_collection": "my-collection",
                    "rules": [
                        {
                            "collection": "source",
                            "name": "Source code",
                            "file_group": ["general-source"],
                            "path": ["src/"],
                        }
                    ],
                }
            }
        )
        assert errors == []

    def test_file_type_group_minimal(self):
        errors = validate_config(
            {
                "file_type_groups": {
                    "custom": {
                        "extensions": [".xyz"],
                        "chunker": {"strategy": "chonkie-recursive"},
                    }
                }
            }
        )
        assert errors == []

    def test_file_type_group_full(self):
        errors = validate_config(
            {
                "file_type_groups": {
                    "my-group": {
                        "extensions": [".abc", ".def"],
                        "mime_types": ["text/plain"],
                        "detectors": ["mumps-labels"],
                        "priority": 25,
                        "extractor": "raw-text",
                        "chunker": {
                            "strategy": "chonkie-code",
                            "chunk_size": 1024,
                            "language": "python",
                        },
                        "max_file_size": 10485760,
                    }
                }
            }
        )
        assert errors == []

    def test_url_resolvers_chain(self):
        errors = validate_config(
            {
                "url_resolvers": [
                    {"type": "httrack"},
                    {
                        "type": "pattern",
                        "match": "^WorldVistA/([^/]+)/(.+)$",
                        "template": "https://github.com/WorldVistA/{1}/blob/HEAD/{2}",
                        "strip_prefix": "source/",
                    },
                    {"type": "domain-first", "strip_prefix": "source/"},
                ]
            }
        )
        assert errors == []

    def test_kubernetes_full(self):
        errors = validate_config(
            {
                "kubernetes": {
                    "namespace": "thresher",
                    "image": "thresher:latest",
                    "image_pull_policy": "Always",
                    "runner_resources": {
                        "requests": {"cpu": "1", "memory": "4Gi"},
                        "limits": {"cpu": "4", "memory": "8Gi"},
                    },
                    "max_parallelism": 20,
                    "node_selector": {"gpu": "true"},
                    "tolerations": [{"key": "gpu", "operator": "Exists"}],
                    "backoff_limit": 5,
                    "ttl_seconds_after_finished": 7200,
                }
            }
        )
        assert errors == []

    def test_queue_settings(self):
        errors = validate_config({"queue": {"batch_size": 500, "lease_timeout": 300}})
        assert errors == []

    def test_embedding_settings(self):
        errors = validate_config(
            {
                "embedding": {
                    "model": "custom-model",
                    "vector_size": 768,
                    "vector_name": "custom",
                    "max_tokens": 256,
                }
            }
        )
        assert errors == []

    def test_archive_exclude_extensions(self):
        errors = validate_config(
            {
                "processing": {
                    "archive_exclude_extensions": [".jar", ".war", ".nupkg"],
                }
            }
        )
        assert errors == []

    def test_expansion_config_valid(self):
        errors = validate_config(
            {
                "processing": {
                    "max_expansion_parallelism": 10,
                    "upload_batch_size": 100,
                    "expansion_timeout": 7200,
                }
            }
        )
        assert errors == []


class TestSchemaRejectsInvalidExpansion:
    """Schema rejects invalid expansion config values."""

    def test_zero_expansion_parallelism(self):
        errors = validate_config({"processing": {"max_expansion_parallelism": 0}})
        assert any("max_expansion_parallelism" in e for e in errors)

    def test_negative_upload_batch_size(self):
        errors = validate_config({"processing": {"upload_batch_size": -1}})
        assert any("upload_batch_size" in e for e in errors)

    def test_string_expansion_timeout(self):
        errors = validate_config({"processing": {"expansion_timeout": "slow"}})
        assert any("expansion_timeout" in e for e in errors)
