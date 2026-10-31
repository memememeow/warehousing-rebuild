"""Verify CLI startup boundaries; reserve reader and order grammar coverage.

Future cases cover launch-relative and config-relative references, absolute
inputs, missing files, malformed JSON and CSVs, mixed case, normalization
collisions, SKU coverage, employee roles and preserved order source context.

Launcher cases cover omitted --debug (default F), explicit T/F, lowercase
and other invalid values, missing values and help documenting the default.
Argument rejection and help must precede file reads, state construction and
run-folder creation. Debug verbosity adds no WarehouseConfig field.
"""

from __future__ import annotations

import io
import os
import runpy
import subprocess
import sys
import tempfile
import unittest
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from warehouse.launcher import _parse_arguments, warehouse


PROJECT_ROOT = Path(__file__).resolve().parent.parent


class LauncherTests(unittest.TestCase):
    """Protect argument validation, path handling and the directory bootstrap."""

    def test_debug_default_and_explicit_choices(self) -> None:
        for options, expected in (
            ([], "F"), (["--debug", "F"], "F"), (["--debug", "T"], "T")
        ):
            with self.subTest(options=options):
                arguments = _parse_arguments(
                    ["--config", "Config.JSON", "--orders", "Orders.TXT", *options]
                )
                self.assertEqual(arguments.debug, expected)

    def test_parser_preserves_supplied_path_text(self) -> None:
        arguments = _parse_arguments(
            ["--config", "./Input Files/../Config.JSON", "--orders", "./Orders.TXT"]
        )
        self.assertEqual(arguments.config, "./Input Files/../Config.JSON")
        self.assertEqual(arguments.orders, "./Orders.TXT")

    def test_help_and_argument_errors_stop_before_startup(self) -> None:
        required = ["--config", "Config.JSON", "--orders", "Orders.TXT"]
        cases = [
            (["--help"], 0),
            ([], 2),
            (["--config", "Config.JSON"], 2),
            (["--orders", "Orders.TXT"], 2),
            (["--config"], 2),
            ([*required, "--orders"], 2),
            ([*required, "--debug"], 2),
            ([*required, "--unknown"], 2),
        ]
        cases.extend(
            ([*required, "--debug", token], 2)
            for token in ("t", "f", "True", "False", "1", "", "TF")
        )
        boundaries = (
            "builtins.open",
            "io.open",
            "os.mkdir",
            "os.chdir",
            "warehouse.launcher.Path.cwd",
            "warehouse.launcher.Path.resolve",
            "warehouse.configuration.config_loader.ConfigLoader.load_config",
            "warehouse.file_io.order_reader.OrderReader.read_orders",
            "warehouse.file_io.output_writer.OutputWriter.create_output_directory",
            "warehouse.domain.warehouse_state.WarehouseState",
            "warehouse.workflow.warehouse_application.WarehouseApplication.run",
        )
        for argv, expected in cases:
            with self.subTest(argv=argv), ExitStack() as stack:
                mocks = [stack.enter_context(patch(target)) for target in boundaries]
                stdout = stack.enter_context(redirect_stdout(io.StringIO()))
                stderr = stack.enter_context(redirect_stderr(io.StringIO()))
                with self.assertRaises(SystemExit) as caught:
                    warehouse(argv)
                self.assertEqual(caught.exception.code, expected)
                for boundary in mocks:
                    boundary.assert_not_called()
                if expected == 0:
                    self.assertIn("--config PATH", stdout.getvalue())
                    self.assertIn("--orders PATH", stdout.getvalue())
                    self.assertIn("--debug {T,F}", stdout.getvalue())
                    self.assertIn("default: F", " ".join(stdout.getvalue().split()))
                    self.assertEqual(stderr.getvalue(), "")
                else:
                    self.assertEqual(stdout.getvalue(), "")
                    self.assertIn("error:", stderr.getvalue())

    def test_valid_startup_captures_launch_directory_once(self) -> None:
        original_directory = Path.cwd()
        with (
            patch("warehouse.launcher.Path.cwd", return_value=original_directory) as cwd,
            patch("os.chdir") as chdir,
            patch("builtins.open") as open_file,
            patch("io.open") as path_open,
            patch("os.mkdir") as mkdir,
            redirect_stderr(io.StringIO()) as stderr,
        ):
            result = warehouse(
                ["--config", "./Input/../Config.JSON", "--orders", "./Orders.TXT"]
            )
        self.assertEqual(result, 1)
        cwd.assert_called_once_with()
        for boundary in (chdir, open_file, path_open, mkdir):
            boundary.assert_not_called()
        self.assertIn("supplied='./Input/../Config.JSON'", stderr.getvalue())
        self.assertIn(
            f"resolved={str(original_directory / 'Config.JSON')!r}", stderr.getvalue()
        )
        self.assertIn(
            f"resolved={str(original_directory / 'Orders.TXT')!r}", stderr.getvalue()
        )
        self.assertEqual(Path.cwd(), original_directory)

    def test_executable_paths_and_no_artifacts_from_different_directories(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture_root = Path(temporary).resolve()
            inputs = fixture_root / "Input Files"
            inputs.mkdir()
            config = inputs / "Config.JSON"
            orders = inputs / "Orders.TXT"
            config.write_text("Intentionally unprocessed configuration", encoding="utf-8")
            orders.write_text("Intentionally unprocessed orders", encoding="utf-8")
            launch_directory = fixture_root / "Other Launch"
            launch_directory.mkdir()
            before = sorted(fixture_root.rglob("*"))
            for launch in (fixture_root, launch_directory):
                relative_config = os.path.relpath(config, launch)
                relative_orders = os.path.relpath(orders, launch)
                cases = [(["--help"], 0), (["--debug", "invalid"], 2)]
                for config_text, orders_text in (
                    (relative_config, relative_orders),
                    (str(config), str(orders)),
                ):
                    for debug in ([], ["--debug", "F"], ["--debug", "T"]):
                        cases.append(
                            (["--config", config_text, "--orders", orders_text, *debug], 1)
                        )
                for argv, expected in cases:
                    with self.subTest(launch=launch, argv=argv):
                        result = subprocess.run(
                            [sys.executable, str(PROJECT_ROOT / "warehouse"), *argv],
                            cwd=launch,
                            capture_output=True,
                            text=True,
                            check=False,
                        )
                        self.assertEqual(result.returncode, expected, result.stderr)
                        if expected == 1:
                            self.assertEqual(result.stdout, "")
                            self.assertIn(
                                "simulation startup is not implemented", result.stderr
                            )
                            self.assertIn(f"supplied={argv[1]!r}", result.stderr)
                            self.assertIn(f"resolved={str(config)!r}", result.stderr)
                            self.assertIn(f"supplied={argv[3]!r}", result.stderr)
                            self.assertIn(f"resolved={str(orders)!r}", result.stderr)
                        self.assertEqual(sorted(fixture_root.rglob("*")), before)
            self.assertEqual(
                config.read_text(encoding="utf-8"),
                "Intentionally unprocessed configuration",
            )
            self.assertEqual(
                orders.read_text(encoding="utf-8"), "Intentionally unprocessed orders"
            )

    def test_bootstrap_forwards_arguments_and_exit_result_without_chdir(self) -> None:
        argv = [
            "warehouse", "--config", "Config.JSON", "--orders", "Orders.TXT", "--debug", "T"
        ]
        original_directory = Path.cwd()
        with (
            patch("warehouse.launcher.warehouse", return_value=17) as launch,
            patch.object(sys, "argv", argv),
            patch.object(sys, "path", sys.path.copy()),
            patch("os.chdir") as chdir,
        ):
            with self.assertRaises(SystemExit) as caught:
                runpy.run_path(
                    str(PROJECT_ROOT / "warehouse" / "__main__.py"), run_name="__main__"
                )
            self.assertEqual(caught.exception.code, 17)
            launch.assert_called_once_with(argv[1:])
            self.assertEqual(sys.path[0], str(PROJECT_ROOT))
            chdir.assert_not_called()
        self.assertEqual(Path.cwd(), original_directory)


# TODO: Add deterministic reader fixtures and rejection cases with source context.
