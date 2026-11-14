"""Point d'entrée de la version empaquetée (PyInstaller)."""

import sys

from correcteur.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
