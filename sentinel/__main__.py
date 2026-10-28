import sys

for _s in (sys.stdout, sys.stderr):  # Windows consoles / redirected logs default to cp1252
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

from .cli import main  # noqa: E402

main()
