#!/usr/bin/env python3
"""Reconcile WireGuard handshakes and application pod health with the website."""
import argparse
import logging
import os
import sys
import time

import agent
import range_ops


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default=os.environ.get("JENGASEC_BASE", agent.DEFAULT_BASE))
    parser.add_argument("--token", default=os.environ.get("JENGASEC_DEPLOY_TOKEN", ""))
    parser.add_argument("--competition", type=int, default=os.environ.get("JENGASEC_COMPETITION_ID"))
    parser.add_argument(
        "--wg-command",
        default=os.environ.get("JENGASEC_WG_DUMP_COMMAND", "wg show all dump"),
        help="WireGuard dump command as argv (no shell); configure SSH/API access as needed.",
    )
    parser.add_argument(
        "--active-seconds",
        type=int,
        default=180,
        help="Maximum handshake age to count a peer as Active.",
    )
    parser.add_argument("--watch", action="store_true", help="Repeat at an interval instead of running once.")
    parser.add_argument("--interval", type=int, default=60)
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    if args.active_seconds < 1:
        parser.error("--active-seconds must be positive")
    if args.watch and args.interval < 1:
        parser.error("--interval must be positive")
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )
    try:
        api = range_ops.api_client(args.base, args.token)
        while True:
            range_ops.reconcile(
                api,
                wg_command=args.wg_command,
                active_seconds=args.active_seconds,
                competition=args.competition,
            )
            if not args.watch:
                return 0
            time.sleep(args.interval)
    except KeyboardInterrupt:
        logging.getLogger("jengasec-range").info("Stopping.")
        return 0
    except (agent.Infra, agent.Stop) as exc:
        logging.getLogger("jengasec-range").error("%s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
