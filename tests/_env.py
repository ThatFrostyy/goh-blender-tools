"""Shared setup for the headless test scripts (run them with Blender, see tests/README.md)."""
import os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.environ.get("GOH_TOOLS", os.path.join(HERE, "..", "goh_tools.py"))
GAME = os.environ.get("GOH_GAME_DIR")
if not GAME or not os.path.isdir(os.path.join(GAME, "resource")):
    sys.exit("Set the environment variable GOH_GAME_DIR to the game folder "
             "(the one that contains 'resource' and 'mods'), e.g. D:/Steam/steamapps/common/Call to Arms - Gates of Hell")
GAME = GAME.replace("\\", "/")
os.environ.setdefault("GOH_CACHE_DIR", os.path.join(HERE, ".cache"))
OUT = os.path.join(HERE, "out")
os.makedirs(OUT, exist_ok=True)


def load_tools(namespace):
    """exec goh_tools.py into the given globals (the same thing the add-on does when installed)"""
    with open(TOOLS, encoding="utf-8") as f:
        exec(compile(f.read(), "goh_tools.py", "exec"), namespace)
