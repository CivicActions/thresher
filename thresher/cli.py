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

    # Runner subcommand
    runner = subparsers.add_parser("runner", help="Process files from queue")
    runner.add_argument("--runner-id", required=True, help="Unique runner identifier")

    args = parser.parse_args(argv)
    setup_logging(level=args.log_level)

    config = load_config(args.config)

    if args.command == "controller":
        return _run_controller(config, args)
    elif args.command == "runner":
        return _run_runner(config, args)

    return 1


def _run_controller(config, args) -> int:
    """Execute the controller workflow."""
    from thresher.controller.queue_builder import build_queue
    from thresher.controller.scanner import scan_files
    from thresher.runner.processor import create_source_provider

    config.force = getattr(args, "force", False)
    source = create_source_provider(config)

    # Scan
    items = scan_files(source, config)

    if args.dry_run:
        print(f"Dry run: would queue {len(items)} files")
        return 0

    # Build queue
    batch_ids = build_queue(
        items,
        source,
        queue_prefix=config.source.gcs.queue_prefix,
        batch_size=config.queue.batch_size,
    )

    print(f"Created {len(batch_ids)} batches with {len(items)} files")

    # Local mode: run embedded runner
    if args.local:
        return _run_local(config, source)

    return 0


def _run_local(config, source) -> int:
    """Run an embedded runner after queue building."""
    from thresher.embedder import Embedder
    from thresher.runner.loop import RunnerLoop
    from thresher.runner.processor import create_destination_provider
    from thresher.types import ProcessingStatus

    destination = create_destination_provider(config)
    embedder = Embedder(
        model_name=config.embedding.model,
        max_tokens=config.embedding.max_tokens,
    )
    embedder.preload()

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


def _run_runner(config, args) -> int:
    """Execute the runner workflow."""
    from thresher.embedder import Embedder
    from thresher.runner.loop import RunnerLoop
    from thresher.runner.processor import create_destination_provider, create_source_provider
    from thresher.types import ProcessingStatus

    source = create_source_provider(config)
    destination = create_destination_provider(config)
    embedder = Embedder(
        model_name=config.embedding.model,
        max_tokens=config.embedding.max_tokens,
    )
    embedder.preload()

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
