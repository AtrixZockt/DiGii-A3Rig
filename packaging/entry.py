"""PyInstaller entry point.

pip normally generates the `a3rig` console script from the `[project.scripts]` entry in
pyproject.toml. A frozen build has no such generator, so this module stands in for it and
does nothing beyond calling the same function.
"""

from a3rig.cli import main

if __name__ == "__main__":
    main()
