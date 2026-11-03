"""Entrada de `jota-gw` y de `python -m jota_gateway.cli`."""


def main() -> None:
    from jota_gateway.cli.app import cli

    cli(prog_name="jota-gw")


if __name__ == "__main__":
    main()
