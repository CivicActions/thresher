"""CLI entrypoint for Thresher — controller and runner subcommands."""

from __future__ import annotations

import argparse

from thresher.config import load_config
from thresher.logging_config import setup_logging


def main(argv: list[str] | None = None) -> int:
    """Main CLI entrypoint."""
    parser = argparse.ArgumentParser(
        prog="thresher",
        description="Cloud native document extraction and indexing pipeline",
    )
    parser.add_argument(
        "--config",
        "-c",
        help="Path to YAML config file",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Log level (default: INFO)",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # Controller subcommand
    ctrl = subparsers.add_parser("controller", help="Scan files, build queue")
    ctrl.add_argument("--dry-run", action="store_true", help="Report without processing")
    ctrl.add_argument("--local", action="store_true", help="Run embedded runner after queue build")
    ctrl.add_argument("--k8s-deploy", action="store_true", help="Create runner K8s Jobs")
    ctrl.add_argument("--k8s-manifest-out", help="Export runner manifests to file")
    ctrl.add_argument("--force", action="store_true", help="Force reprocess all files")
    ctrl.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit the number of files to process (for testing)",
    )

    # Runner subcommand
    runner = subparsers.add_parser("runner", help="Process files from queue")
    runner.add_argument("--runner-id", required=True, help="Unique runner identifier")
    runner.add_argument("--force", action="store_true", help="Force reprocess all files")

    # Expander subcommand
    exp = subparsers.add_parser("expander", help="Expand a single archive")
    exp.add_argument("--archive-path", required=True, help="GCS path of archive to expand")
    exp.add_argument("--force", action="store_true", help="Re-expand even if record exists")

    # Status subcommand
    subparsers.add_parser("status", help="Show pipeline queue and indexing status")

    # MCP config subcommand
    subparsers.add_parser(
        "mcp-config",
        help="Output MCP server configuration JSON derived from pipeline config",
    )

    args = parser.parse_args(argv)
    setup_logging(level=args.log_level)

    config = load_config(args.config)

    if args.command == "controller":
        return _run_controller(config, args)
    elif args.command == "runner":
        return _run_runner(config, args)
    elif args.command == "expander":
        return _run_expander(config, args)
    elif args.command == "status":
        return _run_status(config)
    elif args.command == "mcp-config":
        return _run_mcp_config(config)

    return 1


def _run_controller(config, args) -> int:
    """Execute the controller workflow.

    Two-phase flow: (1) scan direct files + expand archives, (2) build queue from all files.
    """
    import logging

    from thresher.controller.queue_builder import build_queue
    from thresher.controller.scanner import scan_direct_files, scan_expanded_files, scan_summary
    from thresher.runner.processor import create_source_provider

    logger = logging.getLogger("thresher.cli")
    config.force = getattr(args, "force", False)

    # Validate mutually exclusive modes
    modes = sum(
        [
            getattr(args, "local", False),
            getattr(args, "k8s_deploy", False),
            bool(getattr(args, "k8s_manifest_out", None)),
        ]
    )
    if modes > 1:
        print("Error: --local, --k8s-deploy, and --k8s-manifest-out are mutually exclusive")
        return 1

    source = create_source_provider(config)

    # Phase 1: Scan for direct files and detect archives
    items, archives = scan_direct_files(source, config)

    # Phase 1b: Expand archives (parallel) if any found
    if archives:
        from thresher.controller.expansion_orchestrator import ExpansionOrchestrator

        orch = ExpansionOrchestrator(config, source)

        if getattr(args, "k8s_deploy", False):
            expansion_result = orch.expand_k8s(archives)
        else:
            expansion_result = orch.expand_local(archives)

        if expansion_result.failed_archives:
            logger.warning("Failed archives: %s", ", ".join(expansion_result.failed_archives))

        # Phase 1c: Scan expanded files
        expanded_items = scan_expanded_files(source, config)
        items.extend(expanded_items)

    if args.limit is not None and len(items) > args.limit:
        items = items[: args.limit]

    if args.dry_run:
        summary = scan_summary(items)
        print("Dry run summary:")
        print(f"  Total files: {summary['total_files']}")
        print(f"  Total size: {summary['total_size_bytes'] / 1048576:.1f} MB")
        print("  By file type group:")
        for grp, count in sorted(summary["by_group"].items()):
            print(f"    {grp}: {count}")
        print("  By source type:")
        for stype, count in sorted(summary["by_source_type"].items()):
            print(f"    {stype}: {count}")
        return 0

    # Phase 2: Build queue from all files (direct + expanded)
    batch_ids = build_queue(
        items,
        source,
        queue_prefix=config.source.gcs.queue_prefix,
        batch_size=config.queue.batch_size,
    )

    from thresher.controller.queue_builder import queue_summary

    qsummary = queue_summary(batch_ids, items)
    print(
        f"Controller summary: {qsummary['total_files']} files scanned, "
        f"{qsummary['batches_created']} batches created"
    )

    if not batch_ids:
        return 0

    # Local mode: run embedded runner
    if args.local:
        return _run_local(config, source)

    if args.k8s_deploy:
        from thresher.controller.k8s_orchestrator import K8sOrchestrator

        orchestrator = K8sOrchestrator(config, batch_ids)
        created = orchestrator.deploy_jobs()
        print(f"Deployed {len(created)} runner Jobs")
        return 0

    if args.k8s_manifest_out:
        from thresher.controller.k8s_orchestrator import K8sOrchestrator

        orchestrator = K8sOrchestrator(config, batch_ids)
        orchestrator.export_manifests(args.k8s_manifest_out)
        print(f"Exported manifests to {args.k8s_manifest_out}")
        return 0

    # Default: queue-only mode
    return 0


def _run_local(config, source) -> int:
    """Run an embedded runner after queue building."""
    from thresher.embedder import MultiModelEmbedder
    from thresher.runner.loop import RunnerLoop
    from thresher.runner.processor import create_destination_provider
    from thresher.types import ProcessingStatus

    destination = create_destination_provider(config)
    embedder = MultiModelEmbedder(models=config.embedding.models)

    try:
        loop = RunnerLoop(
            runner_id="local-runner",
            source=source,
            destination=destination,
            embedder=embedder,
            config=config,
        )
        results = loop.run()

        indexed = sum(1 for r in results if r.status == ProcessingStatus.INDEXED)
        skipped = sum(1 for r in results if r.status == ProcessingStatus.SKIPPED)
        failed = sum(1 for r in results if r.status == ProcessingStatus.FAILED)
        print(f"Local run complete: {indexed} indexed, {skipped} skipped, {failed} failed")
        return 0 if failed == 0 else 1
    finally:
        destination.close()


def _run_expander(config, args) -> int:
    """Expand a single archive (invoked by K8s expansion jobs)."""
    import logging

    from thresher.controller.archive_expander import ArchiveExpander
    from thresher.runner.processor import create_source_provider

    logger = logging.getLogger("thresher.cli")
    source = create_source_provider(config)
    archive_path = args.archive_path

    expander = ArchiveExpander(
        source=source,
        expanded_prefix=config.source.gcs.expanded_prefix,
        max_depth=config.processing.archive_depth,
        exclude_extensions=config.processing.archive_exclude_extensions,
        upload_batch_size=config.processing.upload_batch_size,
    )

    # Check idempotency (unless --force)
    if not getattr(args, "force", False):
        record = expander._load_expansion_record(archive_path)
        if record is not None:
            logger.info(
                "Archive already expanded: %s (%d members) — skipping",
                archive_path,
                record.member_count,
            )
            return 0

    try:
        results = expander._expand_single(archive_path, depth=0)
        logger.info("Expanded %s: %d files", archive_path, len(results))
        return 0
    except Exception as e:
        logger.error("Failed to expand %s: %s", archive_path, e)
        return 1


def _run_status(config) -> int:
    """Show pipeline queue and Qdrant collection status."""
    from thresher.controller.status import format_status, get_pipeline_status
    from thresher.runner.processor import create_source_provider

    source = create_source_provider(config)
    status = get_pipeline_status(source, config)
    print(format_status(status))
    return 0


def _run_runner(config, args) -> int:
    """Execute the runner workflow."""
    from thresher.embedder import MultiModelEmbedder
    from thresher.runner.loop import RunnerLoop
    from thresher.runner.processor import create_destination_provider, create_source_provider
    from thresher.types import ProcessingStatus

    config.force = getattr(args, "force", False)
    source = create_source_provider(config)
    destination = create_destination_provider(config)
    embedder = MultiModelEmbedder(models=config.embedding.models)

    try:
        loop = RunnerLoop(
            runner_id=args.runner_id,
            source=source,
            destination=destination,
            embedder=embedder,
            config=config,
        )
        results = loop.run()

        indexed = sum(1 for r in results if r.status == ProcessingStatus.INDEXED)
        skipped = sum(1 for r in results if r.status == ProcessingStatus.SKIPPED)
        failed = sum(1 for r in results if r.status == ProcessingStatus.FAILED)
        print(
            f"Runner {args.runner_id} complete: "
            f"{indexed} indexed, {skipped} skipped, {failed} failed"
        )
        return 0 if failed == 0 else 1
    finally:
        destination.close()


def _run_mcp_config(config) -> int:
    """Output MCP server configuration JSON derived from the pipeline config.

    Walks routing rules to enumerate all collections and their assigned embedding
    models, then outputs a JSON object suitable for configuring the MCP server.
    """
    import json


    embedding = config.embedding
    default_model_name = embedding.default

    # Enumerate all collections from routing rules + the default collection
    # collection_name -> model_name (first rule wins for a given collection)
    collection_models: dict[str, str] = {}

    for rule in config.routing.rules:
        col = rule.collection
        if col and col not in collection_models:
            model_name = rule.embedding or default_model_name
            collection_models[col] = model_name

    # Add default collection if not already in rules
    default_col = config.routing.default_collection
    if default_col and default_col not in collection_models:
        collection_models[default_col] = default_model_name

    collections = []
    for col_name, model_name in collection_models.items():
        model_cfg = embedding.models.get(model_name)
        if model_cfg is None:
            continue
        collections.append({
            "name": col_name,
            "model": model_cfg.model,
            "vector_name": model_cfg.vector_name,
            "vector_size": model_cfg.vector_size,
            "query_prefix": model_cfg.query_prefix,
        })

    output = {
        "qdrant_url": config.destination.qdrant.url,
        "qdrant_api_key": config.destination.qdrant.api_key,
        "default_collection": default_col,
        "read_only": True,
        "collections": collections,
    }

    print(json.dumps(output, indent=2))
    return 0
