"""Run all (or selected) data collectors."""

import argparse
import logging
import sys
from pathlib import Path

# Allow running as a plain script: python src/collectors/run_all.py
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.collectors.fingrid_collector import FingridCollector
from src.collectors.mml_collector import MMLCollector
from src.collectors.natura2000_collector import Natura2000Collector
from src.collectors.osm_collector import OSMCollector
from src.collectors.syke_collector import SYKECollector
from src.config import RAW_DATA_DIR

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Registry: key -> (human label, collector class)
# Keys are the values accepted by --collectors on the CLI.
COLLECTORS: dict[str, tuple[str, type]] = {
    "osm": ("OpenStreetMap infrastructure", OSMCollector),
    "natura2000": ("Natura 2000 protected areas", Natura2000Collector),
    "syke": ("SYKE flood hazard zones", SYKECollector),
    "mml": ("MML land parcels  [requires MML_API_KEY]", MMLCollector),
    "fingrid": ("Fingrid grid capacity [stub - manual workflow]", FingridCollector),
}


def main(enabled: list[str] | None = None) -> tuple[dict, list]:
    """Run data collectors sequentially.

    Args:
        enabled: List of collector keys to run (see COLLECTORS).
                 Pass None (default) to run all collectors.

    Returns:
        Tuple of (results dict, errors list).
    """
    selected = enabled or list(COLLECTORS.keys())

    logger.info("=" * 70)
    logger.info("KRIOS - Data collection pipeline")
    logger.info(f"Collectors : {', '.join(selected)}")
    logger.info(f"Output dir : {RAW_DATA_DIR}")
    logger.info("=" * 70)

    results: dict = {}
    errors: list = []

    for i, key in enumerate(selected, 1):
        label, cls = COLLECTORS[key]
        logger.info(f"\n[{i}/{len(selected)}] {label}")
        logger.info("-" * 60)
        try:
            results[key] = cls().collect()
            logger.info(f"  [ok] {key} done")
        except Exception as e:
            logger.error(f"  [x] {key} failed: {e}")
            errors.append((key, e))

    # ── Summary ──────────────────────────────────────────────────────────────
    logger.info("\n" + "=" * 70)
    logger.info("SUMMARY")
    logger.info("=" * 70)

    for key, data in results.items():
        if isinstance(data, dict):
            for layer, gdf in data.items():
                if gdf is not None:
                    logger.info(f"  [ok] {key}/{layer}: {len(gdf)} features")
                else:
                    logger.info(f"  [x] {key}/{layer}: no data returned")
        elif data is not None:
            logger.info(f"  [ok] {key}: {len(data)} features")
        else:
            logger.info(f"  [x] {key}: no data returned")

    if errors:
        logger.warning(f"\n{len(errors)} collector(s) failed:")
        for key, err in errors:
            logger.warning(f"  - {key}: {err}")
    else:
        logger.info("\nAll collectors completed successfully.")

    logger.info(f"\nData saved to: {RAW_DATA_DIR}")
    logger.info("=" * 70)

    return results, errors


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run KRIOS data collectors.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="\n".join(
            ["Available collectors:"]
            + [f"  {k:12s} - {label}" for k, (label, _) in COLLECTORS.items()]
        ),
    )
    parser.add_argument(
        "--collectors",
        nargs="+",
        choices=list(COLLECTORS.keys()),
        metavar="NAME",
        help="Collectors to run (default: all). "
        f"Choices: {', '.join(COLLECTORS.keys())}",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    args = _parse_args()
    _, errors = main(enabled=args.collectors)
    sys.exit(len(errors))
