import argparse


def argument_parser(config):
    parser = argparse.ArgumentParser(prog="start-server",
        description="< command description >",
        formatter_class = lambda prog: argparse.HelpFormatter(prog, max_help_position=40))
    verbosity = parser.add_mutually_exclusive_group()
    verbosity.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="increase output verbosity",
    )
    verbosity.add_argument(
        "-q", "--quiet",
        action="store_true",
        help="decrease output verbosity",
    )
    parser.add_argument(
        "-H", "--host",
        default=config["host"],
        help="service IP address",
    )
    parser.add_argument(
        "-p", "--port",
        type=int,
        default=config["port"],
        help="service port",
    )
    parser.add_argument(
        "-s", "--storage",
        default=config["storage"],
        help="storage directory path",
    )
    return parser


def parse_arguments(config):
    parser = argument_parser(config)
    arguments = parser.parse_args()
    arguments.protocol = config["protocol"] #TODO: todos los protocolos deben de ser validos.
    arguments.verbosity = 1 if arguments.verbose else -1 if arguments.quiet else config["verbosity"]
    return arguments