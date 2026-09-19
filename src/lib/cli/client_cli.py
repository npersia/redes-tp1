import argparse

from lib.configuration.client_config import load_config


def add_common_arguments(parser, config):
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
        default=config["HOST"],
        metavar="ADDR",
        help="server IP address",
    )
    parser.add_argument(
        "-p", "--port",
        type=int,
        default=config["CLIENT_PORT"],
        metavar="PORT",
        help="server port",
    )


def add_protocol_argument(parser, config):
    parser.add_argument(
        "-r", "--protocol",
        default=config["PROTOCOL"],
        metavar="protocol",
        help="error recovery protocol",
    )


def upload_parser(config):
    parser = argparse.ArgumentParser(
        prog="upload",
        description="< command description >",
        formatter_class = lambda prog: argparse.HelpFormatter(prog, max_help_position=40))
    add_common_arguments(parser, config)
    parser.add_argument("-s", "--src", default=config["SRC"], metavar="FILEPATH", help="source file path")
    parser.add_argument("-n", "--name", metavar="FILENAME", help="file name")
    add_protocol_argument(parser, config)
    return parser


def download_parser(config):
    parser = argparse.ArgumentParser(
        prog="download",
        description="< command description >",
        formatter_class = lambda prog: argparse.HelpFormatter(prog, max_help_position=40))
    add_common_arguments(parser, config)
    parser.add_argument("-d", "--dst", default=config["DST"], metavar="FILEPATH", help="destination file path")
    parser.add_argument("-n", "--name", metavar="FILENAME", help="file name")
    add_protocol_argument(parser, config)
    return parser


def parse_upload_arguments():
    config = load_config()
    arguments = upload_parser(config).parse_args()
    verbosity = int(config["VERBOSITY"]) if "VERBOSITY" in config else 0
    arguments.verbosity = 1 if arguments.verbose else -1 if arguments.quiet else verbosity
    return arguments


def parse_download_arguments():
    config = load_config()
    arguments = download_parser(config).parse_args()
    verbosity = int(config["VERBOSITY"]) if "VERBOSITY" in config else 0
    arguments.verbosity = 1 if arguments.verbose else -1 if arguments.quiet else verbosity
    return arguments