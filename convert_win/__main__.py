import sys
if len(sys.argv) > 1 and sys.argv[1] == "--cli":
    from .cli import run
    sys.exit(run(sys.argv[2:]))
from .app import main
main(sys.argv[1:])
