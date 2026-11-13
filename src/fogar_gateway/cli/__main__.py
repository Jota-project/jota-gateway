"""Entrada de `fogar-gw` y de `python -m fogar_gateway.cli`."""


def main() -> None:
    from fogar_gateway.cli.app import cli

    cli(prog_name="fogar-gw")


if __name__ == "__main__":
    main()
