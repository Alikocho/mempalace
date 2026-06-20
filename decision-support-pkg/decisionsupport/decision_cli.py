#!/usr/bin/env python3
"""
decision — Framework-agnostic decision support.

Wraps Cynefin and Delphi under a single entry point.
Use 'decision toggle' to flip between frameworks; all other commands
are forwarded to whichever framework is currently active.

Usage:
    decision toggle                 Flip between cynefin ↔ delphi
    decision framework              Show active framework
    decision framework <name>       Set active framework explicitly
    decision <command> [args...]    Proxy to the active framework's CLI

Examples:
    decision toggle
    decision new "Q3 pricing" -d "What pricing model for Q3?"
    decision list
    decision framework cynefin
"""

import sys

from .decision import FRAMEWORKS, get_framework, set_framework, toggle


def _print_status() -> None:
    fw = get_framework()
    other = next(f for f in FRAMEWORKS if f != fw)
    print(f"\n  decision — active framework: {fw.upper()}")
    print("\n  Commands:")
    print(f"    decision toggle          Switch to {other.title()}")
    print("    decision framework       Show / set active framework")
    print(f"    decision <command>       Forward to {fw.title()} CLI")
    print(f"\n  Run '{fw} --help' for {fw.title()}-specific commands.\n")


def main() -> None:
    if len(sys.argv) < 2:
        _print_status()
        return

    cmd = sys.argv[1]

    # -- toggle ---------------------------------------------------------------
    if cmd == "toggle":
        new_fw = toggle()
        old_fw = next(f for f in FRAMEWORKS if f != new_fw)
        print(f"\n  Switched: {old_fw.title()} → {new_fw.upper()}")
        print(f"  All 'decision' commands now use {new_fw.title()}.\n")
        return

    # -- framework ------------------------------------------------------------
    if cmd == "framework":
        if len(sys.argv) >= 3:
            name = sys.argv[2].lower()
            try:
                set_framework(name)
            except ValueError as exc:
                print(f"Error: {exc}", file=sys.stderr)
                sys.exit(1)
            print(f"\n  Framework set to: {name.upper()}\n")
        else:
            fw = get_framework()
            other = next(f for f in FRAMEWORKS if f != fw)
            print(f"\n  Active framework: {fw.upper()}")
            print(f"  Other available:  {other}")
            print("  To switch:        decision toggle\n")
        return

    # -- proxy to active framework --------------------------------------------
    fw = get_framework()

    # Replace argv[0] so the downstream parser shows the right prog name
    sys.argv[0] = fw

    if fw == "cynefin":
        from .cynefin_cli import main as fw_main
    else:
        from .delphi_cli import main as fw_main

    fw_main()


if __name__ == "__main__":
    main()
