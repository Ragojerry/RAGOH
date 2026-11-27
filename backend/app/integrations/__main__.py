"""python -m app.integrations run [--only elasticsearch|dhis2|digit] [--force]"""

import argparse
import json
import logging

from app.db.session import SessionLocal
from app.integrations.base import run_all


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    ap = argparse.ArgumentParser()
    ap.add_argument("task", choices=["run"])
    ap.add_argument("--only", choices=["elasticsearch", "dhis2", "digit"])
    ap.add_argument("--force", action="store_true", help="ignore every_min")
    a = ap.parse_args()
    with SessionLocal() as db:
        print(json.dumps(run_all(db, only=a.only, force=a.force), default=str))


if __name__ == "__main__":
    main()
