"""
Tests for the niitti.paths module.

:purpose: Verify XDG resolution, environment overrides, and that no read path creates a directory.
"""

import subprocess
import sys
import textwrap

from niitti.paths import cache_dir, config_dir, data_dir, ensure_dir, site_config_dir


def test_xdg_roots_are_honored(tmp_path, monkeypatch):
    """
    Verify that the XDG environment variables select the parent of each directory.

    :return: None
    """
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))

    assert data_dir("meri") == tmp_path / "data" / "meri"
    assert cache_dir("meri") == tmp_path / "cache" / "meri"
    assert config_dir("meri") == tmp_path / "config" / "meri"


def test_config_and_data_dirs_do_not_collide(tmp_path, monkeypatch):
    """
    Verify that a data directory never resolves onto a configuration directory.

    A collision would let a written file outrank the configuration file that the service uses.

    :return: None
    """
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "instance"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "instance" / "data"))

    assert config_dir("meri") != data_dir("meri")


def test_environment_override_applies_to_a_missing_directory(tmp_path, monkeypatch):
    """
    Verify that the override applies also when the directory does not exist yet.

    The command that provisions the data creates the directory later.

    :return: None
    """
    absent = tmp_path / "not-there"
    monkeypatch.setenv("LUOTSI_DATA_DIR", str(absent))

    assert data_dir("luotsi", env_var="LUOTSI_DATA_DIR") == absent
    assert not absent.exists()


def test_empty_override_falls_through_to_xdg(tmp_path, monkeypatch):
    """
    Verify that an empty or blank environment variable does not select an empty path.

    :return: None
    """
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("LUOTSI_DATA_DIR", "   ")

    assert data_dir("luotsi", env_var="LUOTSI_DATA_DIR") == tmp_path / "data" / "luotsi"


def test_override_expands_the_home_marker(tmp_path, monkeypatch):
    """
    Verify that `~` in an override expands to the home directory.

    :return: None
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("LUOTSI_DATA_DIR", "~/models")

    assert data_dir("luotsi", env_var="LUOTSI_DATA_DIR") == tmp_path / "models"


def test_read_paths_create_nothing(tmp_path, monkeypatch):
    """
    Verify that resolution has no effect on the filesystem, so it works on a read-only volume.

    :return: None
    """
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))

    for path in (data_dir("meri"), cache_dir("meri"), config_dir("meri"), site_config_dir("meri")):
        assert not path.exists()
    assert list(tmp_path.iterdir()) == []


def test_ensure_dir_creates_and_repeats(tmp_path):
    """
    Verify that `ensure_dir` creates the parents and accepts a second call.

    :return: None
    """
    target = tmp_path / "a" / "b"

    assert ensure_dir(target) == target
    assert target.is_dir()
    assert ensure_dir(target) == target


def test_paths_import_without_the_optional_stacks():
    """
    Verify that `niitti.paths` imports without structlog, pydantic or OpenTelemetry.

    A dependency-light package must be able to use the resolver. The check runs in a subprocess, because the modules
    are already imported in this one.

    :return: None
    """
    script = textwrap.dedent(
        """
        import sys

        BLOCKED = {"structlog", "pydantic", "pydantic_settings", "opentelemetry", "sentry_sdk"}

        class Blocker:
            def find_spec(self, name, path=None, target=None):
                if name.split(".")[0] in BLOCKED:
                    raise ImportError(f"blocked: {name}")
                return None

        sys.meta_path.insert(0, Blocker())

        from niitti.paths import data_dir

        assert data_dir("luotsi").name == "luotsi"
        print("ok")
        """
    )
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=False)

    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout


def test_lazy_top_level_exports_still_resolve():
    """
    Verify that the lazy `niitti` exports keep working after the import of `niitti.paths`.

    :return: None
    """
    import niitti

    assert callable(niitti.get_logger)
    assert callable(niitti.setup_logging)
    assert "get_logger" in dir(niitti)
