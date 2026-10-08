from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from eda_agent.config import PROJECT_DIR, Settings
from eda_agent.modeling_pipeline import (
    PIPELINE_STAGES,
    ModelingPipeline,
    PipelineConfig,
    PipelineState,
    select_stages,
)
from eda_agent.tools.pmms import PMMSClient


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run or preview the staged MeQLab device-modeling pipeline."
    )
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument(
        "--state",
        type=Path,
        default=PROJECT_DIR / "artifacts" / "modeling_pipeline_state.json",
    )
    parser.add_argument("--stage", choices=PIPELINE_STAGES)
    parser.add_argument("--from-stage", choices=PIPELINE_STAGES)
    parser.add_argument("--through-stage", choices=PIPELINE_STAGES)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually call PMMS. Without this flag, only a dry-run plan is recorded.",
    )
    parser.add_argument(
        "--allow-long-jobs",
        action="store_true",
        help="Permit configured start_optimize/start_qa calls. Never implied by --execute.",
    )
    parser.add_argument(
        "--check-job",
        choices=("optimize", "qa"),
        help="Fetch one status snapshot for a previously recorded asynchronous job.",
    )
    return parser


async def _run(args: argparse.Namespace) -> None:
    config_path = args.config.expanduser().resolve()
    config = PipelineConfig.load(config_path)
    state = PipelineState(args.state, config)
    stages = select_stages(
        stage=args.stage,
        from_stage=args.from_stage,
        through_stage=args.through_stage,
    )

    if args.check_job and not args.execute:
        raise SystemExit("--check-job requires --execute")

    if not args.execute:
        pipeline = ModelingPipeline(
            config,
            state,
            dry_run=True,
            allow_long_jobs=args.allow_long_jobs,
        )
        await pipeline.run(stages, resume=args.resume)
        print(f"Dry-run complete. Plan/state: {state.path}")
        return

    settings = Settings.load(require_models=False)
    async with PMMSClient(settings.pmms_exe, settings.pmms_config) as client:
        pipeline = ModelingPipeline(
            config,
            state,
            client,
            dry_run=False,
            allow_long_jobs=args.allow_long_jobs,
        )
        if args.check_job:
            state.load_for_resume()
            status = await pipeline.refresh_job_status(args.check_job)
            print(f"{args.check_job} job status: {status}")
            return
        await pipeline.run(stages, resume=args.resume)
    print(f"Pipeline stages complete. State: {state.path}")


def main() -> None:
    args = _parser().parse_args()
    if args.stage and (args.from_stage or args.through_stage):
        raise SystemExit("--stage cannot be combined with --from-stage/--through-stage")
    if args.check_job and (args.stage or args.from_stage or args.through_stage):
        raise SystemExit("--check-job cannot be combined with stage-selection flags")
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
