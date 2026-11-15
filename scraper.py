"""Run the scrapers. The hourly scheduled task calls this file, so keep its name.

    python scraper.py                    # every source
    python scraper.py kytary_boutique    # just one (or several, space separated)
    python scraper.py hudebnibazar
"""

import sys
import traceback

from scrapers import hudebnibazar, kytary_boutique
from scrapers.common import log_to_file_if_windowless

SOURCES = {
    "kytary_boutique": kytary_boutique.main,
    "hudebnibazar": hudebnibazar.main,
}


def main(names: list[str]) -> int:
    unknown = [n for n in names if n not in SOURCES]
    if unknown:
        print(f"Unknown source(s): {', '.join(unknown)}. Choose from: {', '.join(SOURCES)}")
        return 2
    failed = 0
    for name in names or SOURCES:
        try:
            SOURCES[name]()
        except Exception:  # one broken source shouldn't stop the others
            failed += 1
            traceback.print_exc()
    return 1 if failed else 0


if __name__ == "__main__":
    log_to_file_if_windowless("scraper")
    sys.exit(main(sys.argv[1:]))
