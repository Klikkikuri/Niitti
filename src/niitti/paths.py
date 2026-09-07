"""
Shared filesystem locations for Klikkikuri services.

Every service keeps its runtime files in XDG directories, resolved through `platformdirs`. The container images point
`XDG_DATA_HOME` and `XDG_CACHE_HOME` into `/app/instance`, which is the only persistent location in the container, so
no service must know a container path.

The functions here NEVER create a directory. Settings load and read paths must work on a read-only volume. Use
:func:`ensure_dir` in the code that writes, usually a provisioning command.

This module imports only the standard library and `platformdirs`. Keep it that way: it is the one part of niitti that
a dependency-light package can import.
"""

import os
from pathlib import Path

from platformdirs import site_config_dir as _site_config_dir
from platformdirs import user_cache_dir, user_config_dir, user_data_dir

from niitti.const import DEFAULT_APP_AUTHOR

__all__ = ["cache_dir", "config_dir", "data_dir", "ensure_dir", "site_config_dir"]


def _override(env_var: str | None) -> Path | None:
    """
    Read a directory override from the environment.

    The override applies also when the directory does not exist yet, because the command that provisions the data
    creates it later.

    :param env_var: Name of the environment variable, or `None` for no override.
    :return: The override directory, or `None` when the variable is not set or is empty.
    """
    if not env_var:
        return None
    value = os.environ.get(env_var, "").strip()
    return Path(value).expanduser() if value else None


def data_dir(app: str, *, env_var: str | None = None) -> Path:
    """
    Directory for the persistent data of an application, such as downloaded models or prompt overrides.

    :param app: Application name, usually the package name.
    :param env_var: Optional environment variable that overrides the location.
    :return: Data directory. It is not created and can be absent.
    """
    return _override(env_var) or Path(user_data_dir(app, DEFAULT_APP_AUTHOR))


def cache_dir(app: str, *, env_var: str | None = None) -> Path:
    """
    Directory for the data of an application that the application can download or calculate again.

    :param app: Application name, usually the package name.
    :param env_var: Optional environment variable that overrides the location.
    :return: Cache directory. It is not created and can be absent.
    """
    return _override(env_var) or Path(user_cache_dir(app, DEFAULT_APP_AUTHOR))


def config_dir(app: str) -> Path:
    """
    Directory for the configuration of an application that belongs to the user.

    :param app: Application name, usually the package name.
    :return: Configuration directory. It is not created and can be absent.
    """
    return Path(user_config_dir(app, DEFAULT_APP_AUTHOR))


def site_config_dir(app: str) -> Path:
    """
    Directory for the configuration of an application that applies to the full system.

    :param app: Application name, usually the package name.
    :return: Configuration directory. It is not created and can be absent.
    """
    return Path(_site_config_dir(app, DEFAULT_APP_AUTHOR))


def ensure_dir(path: Path) -> Path:
    """
    Create a directory and its parents. Use it only in the code that writes, never on a read path.

    :param path: Directory to create.
    :return: The same directory.
    :raises PermissionError: The directory cannot be created. In a container this usually means that the
        `/app/instance` mount belongs to a different user. Set `PUID` and `PGID`, or correct the owner on the host.
    """
    path.mkdir(parents=True, exist_ok=True)
    return path
