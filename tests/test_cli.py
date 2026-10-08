"""Parser-level tests for the CLI -- no hardware, no bridge."""

import sys

import pytest

from awox_mesh.cli import build_parser


def parse(argv):
    original = sys.argv
    sys.argv = ["awox", *argv]
    try:
        return build_parser().parse_args(argv)
    finally:
        sys.argv = original


def test_prog_name_is_usable():
    prog = build_parser().prog
    assert prog not in ("", "cli.py")
    assert not prog.endswith(".py")


@pytest.mark.parametrize("argv", [
    ["--url", "http://x:1", "state"],
    ["state", "--url", "http://x:1"],
])
def test_common_options_work_in_both_positions(argv):
    ns = parse(argv)
    assert ns.command == "state"
    assert ns.url == "http://x:1"


def test_subcommand_does_not_clobber_parent_option():
    ns = parse(["--url", "http://x:1", "state"])
    assert ns.url == "http://x:1"


@pytest.mark.parametrize("argv", [
    ["--direct", "state"],
    ["state", "--direct"],
])
def test_direct_flag_works_in_both_positions(argv):
    assert parse(argv).direct is True


def test_discover_defaults_and_direct():
    assert parse(["discover"]).command == "discover"
    assert parse(["discover"]).direct is False
    assert parse(["discover", "--direct"]).direct is True


def test_api_key_is_parsed_after_subcommand():
    assert parse(["health", "--api-key", "abc"]).api_key == "abc"


def test_brightness_requires_a_percent():
    with pytest.raises(SystemExit):
        parse(["brightness"])


def test_unknown_command_is_rejected():
    with pytest.raises(SystemExit):
        parse(["nonsense"])
