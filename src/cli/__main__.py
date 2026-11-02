"""Entrada de `jota-gw` y de `python -m src.cli`."""


def main() -> None:
    from src.cli.app import cli

    cli(prog_name="jota-gw")


if __name__ == "__main__":
    main()
