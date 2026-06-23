"""Launch the Python Engine Simulator.

    py run.py            # start with the default inline-4
    py run.py --engine 3 # 1 = single, 2 = inline-4, 3 = V8
"""

import argparse

from engine_sim import presets
from engine_sim.app import App


def main():
    ap = argparse.ArgumentParser(description="Engine Simulator — Python Edition")
    ap.add_argument("--engine", choices=sorted(presets.ALL), default="aven",
                    help="preset key to start with (default: Aventador V12)")
    args = ap.parse_args()
    App(args.engine).run()


if __name__ == "__main__":
    main()
