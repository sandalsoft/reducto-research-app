import argparse


def main() -> None:
    p = argparse.ArgumentParser(prog="bench", description="Benchmark Reducto on House Oversight email PDFs.")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="run each doc as digital PDF and as image-only scan PDF")
    r.add_argument("--n", type=int, default=100, help="documents to sample from data/inbox")
    r.add_argument("--seed", type=int, default=42)
    r.add_argument("--max-pages", type=int, default=3, help="process the first N pages of each document")
    r.add_argument("--concurrency", type=int, default=8)
    r.add_argument("--baseline-n", type=int, default=5, help="first N docs run sequentially (uncontended latency)")
    r.add_argument("--max-usd", type=float, default=30.0, help="hard stop on list-price spend for this invocation")
    r.add_argument("--repeat-n", type=int, default=10, help="emails to re-extract for the repeatability check")
    r.add_argument("--fresh", action="store_true", help="discard previous output/*.jsonl first")

    sub.add_parser("report", help="build output/report.md, emails.csv, and citation overlays from output/*.jsonl")

    x = sub.add_parser("reextract", help="re-run Extract on cached parse jobs with the current schema (no parse)")
    x.add_argument("--n", type=int, default=None, help="max documents (both variants)")
    x.add_argument("--concurrency", type=int, default=8)
    x.add_argument("--max-usd", type=float, default=30.0)

    args = p.parse_args()
    if args.cmd == "run":
        from .runner import run_all
        run_all(args)
    elif args.cmd == "reextract":
        from .runner import reextract_all
        reextract_all(args)
    else:
        from .report import build
        build()


if __name__ == "__main__":
    main()
