#!/usr/bin/env python3
"""Process application submissions, freeze images/seeds, or reset a round."""
import argparse
import logging
import os
import sys
import time

import agent
import range_ops


def parser_for():
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument(
        "--process-pending",
        action="store_true",
        help="Run one pass of the existing submission pull/scan/test-deploy agent.",
    )
    actions.add_argument("--freeze", action="store_true", help="Copy passing images and snapshot seed databases.")
    actions.add_argument("--reset-day", type=int, choices=(1, 2, 3), help="Restore a frozen round state.")
    parser.add_argument("--competition", type=int, default=os.environ.get("JENGASEC_COMPETITION_ID"))
    parser.add_argument(
        "--state-dir",
        default=os.environ.get("JENGASEC_STATE_DIR", "/var/lib/jengasec-range"),
        help="Private persistent directory for frozen database snapshots.",
    )
    parser.add_argument("--registry", default=os.environ.get("JENGASEC_REGISTRY"))
    parser.add_argument("--scope-script", default=os.environ.get("JENGASEC_MAKE_SCOPE"))
    parser.add_argument("--base", default=os.environ.get("JENGASEC_BASE", agent.DEFAULT_BASE))
    parser.add_argument("--token", default=os.environ.get("JENGASEC_DEPLOY_TOKEN", ""))
    parser.add_argument("--dry-run", action="store_true", help="Preview freeze or reset without changing range state.")
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser


def main(argv=None):
    args, passthrough = parser_for().parse_known_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )
    if args.process_pending:
        forwarded = ["--once", "--base", args.base]
        if args.token:
            forwarded.extend(["--token", args.token])
        if args.verbose:
            forwarded.append("--verbose")
        return agent.main(forwarded + passthrough)
    if passthrough:
        parser_for().error(f"unrecognized arguments: {' '.join(passthrough)}")
    if not args.competition:
        parser_for().error(
            "Set JENGASEC_COMPETITION_ID or pass --competition for freeze/reset."
        )
    try:
        api = range_ops.api_client(args.base, args.token)
        if args.freeze:
            if args.dry_run:
                range_ops.preview_freeze(api, args.competition)
            else:
                range_ops.freeze(api, args.competition, args.state_dir, args.registry)
        else:
            range_ops.reset_day(
                api,
                args.competition,
                args.reset_day,
                args.state_dir,
                args.registry or os.environ.get("JENGASEC_REGISTRY", ""),
                dry_run=args.dry_run,
                scope_script=args.scope_script,
            )
    except (agent.Infra, agent.Stop) as exc:
        logging.getLogger("jengasec-range").error("%s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
