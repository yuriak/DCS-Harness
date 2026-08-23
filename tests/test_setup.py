from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SETUP_SOURCE = REPOSITORY_ROOT / "tools" / "src" / "py"
sys.path.insert(0, str(SETUP_SOURCE))

import setup as setup_module  # noqa: E402
from setup_support.outputs import atomic_write_text, dump_yaml, write_json  # noqa: E402


class RepositoryDiscoveryTests(unittest.TestCase):
    def test_discovers_repository_from_setup_source(self) -> None:
        discovered = setup_module.discover_repository_root(SETUP_SOURCE)
        self.assertEqual(discovered, REPOSITORY_ROOT)

    def test_missing_repository_marker_raises(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(RuntimeError):
                setup_module.discover_repository_root(Path(temporary))


class StatusTests(unittest.TestCase):
    def test_ready(self) -> None:
        checks = [
            setup_module.CheckResult(
                "example", setup_module.CheckStatus.OK, "ready"
            )
        ]
        self.assertEqual(setup_module.overall_status(checks), "READY")

    def test_ready_with_warnings(self) -> None:
        checks = [
            setup_module.CheckResult(
                "example", setup_module.CheckStatus.WARNING, "warning"
            )
        ]
        self.assertEqual(
            setup_module.overall_status(checks), "READY WITH WARNINGS"
        )

    def test_error_takes_precedence(self) -> None:
        checks = [
            setup_module.CheckResult(
                "warning", setup_module.CheckStatus.WARNING, "warning"
            ),
            setup_module.CheckResult(
                "error", setup_module.CheckStatus.ERROR, "error"
            ),
        ]
        self.assertEqual(setup_module.overall_status(checks), "NOT READY")


class PathValidationTests(unittest.TestCase):
    def test_invalid_dcs_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            missing = Path(temporary) / "missing"
            results = setup_module.check_dcs_install(missing)
        self.assertTrue(setup_module.has_errors(results))

    def test_invalid_saved_games_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            ordinary_directory = Path(temporary) / "ordinary"
            ordinary_directory.mkdir()
            results = setup_module.check_saved_games(ordinary_directory)
        self.assertTrue(setup_module.has_errors(results))

    def test_valid_minimal_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dcs_dir = root / "DCS World"
            saved_games_dir = root / "DCS.openbeta"
            (dcs_dir / "bin-mt").mkdir(parents=True)
            (dcs_dir / "Scripts").mkdir()
            (dcs_dir / "bin-mt" / "DCS.exe").touch()
            (dcs_dir / "Scripts" / "MissionScripting.lua").touch()
            saved_games_dir.mkdir()

            dcs_results = setup_module.check_dcs_install(dcs_dir)
            saved_results = setup_module.check_saved_games(saved_games_dir)

        self.assertFalse(setup_module.has_errors(dcs_results))
        self.assertFalse(setup_module.has_errors(saved_results))


class SubmoduleTests(unittest.TestCase):
    def test_missing_submodules_are_errors(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            results = setup_module.check_submodules(Path(temporary))
        self.assertEqual(len(results), len(setup_module.REQUIRED_SUBMODULES))
        self.assertTrue(all(item.status is setup_module.CheckStatus.ERROR for item in results))


class RuntimeTests(unittest.TestCase):
    def test_dry_run_does_not_create_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository_root = Path(temporary)
            results = setup_module.prepare_runtime(repository_root, dry_run=True)
            self.assertFalse((repository_root / "runtime").exists())
        self.assertTrue(
            all(item.status is setup_module.CheckStatus.NOT_TESTED for item in results)
        )

    def test_runtime_directories_include_memory_placeholder(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository_root = Path(temporary)
            result = setup_module.ensure_runtime_directories(repository_root)
            expected = (
                "generated/grpc",
                "logs",
                "workspace",
                "plugins/py",
                "plugins/lua",
                "memory",
            )
            for relative in expected:
                self.assertTrue(
                    (repository_root / "runtime" / relative).is_dir(),
                    relative,
                )
        self.assertIs(result.status, setup_module.CheckStatus.OK)


class SerializationTests(unittest.TestCase):
    def test_yaml_serialization_preserves_scalar_types(self) -> None:
        rendered = dump_yaml(
            {
                "setup": {"status": "READY", "version": "0.3.0"},
                "grpc": {"port": 50051, "eval_enabled": False},
                "optional": None,
            }
        )
        self.assertIn('status: "READY"', rendered)
        self.assertIn("port: 50051", rendered)
        self.assertIn("eval_enabled: false", rendered)
        self.assertIn("optional: null", rendered)

    def test_atomic_text_and_json_writes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            text_path = root / "nested" / "setup.log"
            report_path = root / "setup_report.json"
            atomic_write_text(text_path, "first\n")
            atomic_write_text(text_path, "second\n")
            write_json(report_path, {"status": "READY", "checks": []})

            self.assertEqual(text_path.read_text(encoding="utf-8"), "second\n")
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "READY")


class EnvironmentConfigTests(unittest.TestCase):
    def test_generated_environment_uses_only_explicit_endpoint_fields(self) -> None:
        inspection = setup_module.GrpcInspection(
            diagnostics=(),
            installed=True,
            installation_dir=Path("/saved-games/DCS-gRPC"),
            config_file=Path("/saved-games/dcs-grpc.lua"),
            version="test",
            bind_host="0.0.0.0",
            port=50051,
            eval_enabled=True,
            autostart=True,
            proto_source=Path("/saved-games/protos"),
            proto_source_kind="installed",
        )
        environment = setup_module.build_environment_data(
            repository_root=Path("/repository"),
            generated_at="now",
            status="READY",
            platform_info=setup_module.PlatformInfo(
                host_os="windows",
                agent_os="wsl",
                is_wsl=True,
                python_executable="python",
                python_version="3.13",
            ),
            dcs_path=Path("/dcs"),
            saved_games_path=Path("/saved-games"),
            grpc_inspection=inspection,
        )

        self.assertEqual(environment["grpc"]["bind_host"], "0.0.0.0")
        self.assertEqual(environment["grpc"]["client_host"], "127.0.0.1")
        self.assertNotIn("host", environment["grpc"])

    def test_reads_existing_player_client_host(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            environment_path = Path(temporary) / "environment.yaml"
            environment_path.write_text(
                "setup:\n  status: READY\ngrpc:\n"
                "  bind_host: \"0.0.0.0\"\n"
                "  client_host: \"172.30.96.1\"\n"
                "  port: 50051\n",
                encoding="utf-8",
            )

            client_host = setup_module.read_existing_client_host(environment_path)

        self.assertEqual(client_host, "172.30.96.1")

    def test_missing_client_host_uses_no_legacy_host_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            environment_path = Path(temporary) / "environment.yaml"
            environment_path.write_text(
                "grpc:\n  host: \"172.30.96.1\"\n  port: 50051\n",
                encoding="utf-8",
            )

            client_host = setup_module.read_existing_client_host(environment_path)

        self.assertIsNone(client_host)


if __name__ == "__main__":
    unittest.main()
