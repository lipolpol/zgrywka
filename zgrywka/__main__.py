import sys

from . import GPU_PROBE_FLAG


def run() -> int:
    if len(sys.argv) == 3 and sys.argv[1] == GPU_PROBE_FLAG:  # test karty graficznej (wersja spakowana)
        from .asr import probe_main

        return probe_main(sys.argv[2])
    if len(sys.argv) > 1 and not sys.argv[1].startswith("-psn_"):  # -psn_… dodaje macOS przy starcie z Findera
        from .cli import main as cli_main

        return cli_main(sys.argv[1:])
    from .app import main

    return main()


if __name__ == "__main__":
    sys.exit(run())
