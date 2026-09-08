"""
Shared filesystem locations for Klikkikuri services.

Every service keeps its runtime files in XDG directories, resolved through `platformdirs`. A container has one
persistent location, and mounts it as the service's data directory: `<APP>_DATA_DIR` names that directory outright,
so `/app/instance` is the data directory rather than the root of one, and no service must know a container path.

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

    An override names the directory itself, not a root to append the application name to: a deployment that says
    where its data lives means exactly that path. It applies also when the directory does not exist yet, because
    the command that provisions the data creates it later.

    :param env_var: Name of the environment variable, or `None` for no override.
    :return: The override directory, or `None` when the variable is not set or is empty.
    """
    if not env_var:
        return None
    value = os.environ.get(env_var, "").strip()
    return Path(value).expanduser() if value else None


def _default_var(app: str, kind: str) -> str:
    """
    The environment variable an application overrides one of its directories with, by convention.

    :param app: Application name.
    :param kind: `DATA` or `CACHE`.
    :return: Variable name, such as `MERI_DATA_DIR`.
    """
    return f"{app.upper().replace('-', '_')}_{kind}_DIR"


def data_dir(app: str, *, env_var: str | None = None) -> Path:
    """
    Directory for the persistent data of an application, such as downloaded models or prompt overrides.

    `<APP>_DATA_DIR` overrides it, and names the directory itself. A container mounts one persistent volume and
    points the variable at it, so the data lands in `/app/instance` rather than in a service-named subdirectory
    of a data root that the volume would have to carry as well.

    :param app: Application name, usually the package name.
    :param env_var: Environment variable that overrides the location. Defaults to `<APP>_DATA_DIR`.
    :return: Data directory. It is not created and can be absent.
    """
    return _override(env_var or _default_var(app, "DATA")) or Path(user_data_dir(app, DEFAULT_APP_AUTHOR))


def cache_dir(app: str, *, env_var: str | None = None) -> Path:
    """
    Directory for the data of an application that the application can download or calculate again.

    `<APP>_CACHE_DIR` overrides it, and names the directory itself. Nothing here has to persist, so a deployment
    that sets nothing gets the user cache directory, wherever that is.

    :param app: Application name, usually the package name.
    :param env_var: Environment variable that overrides the location. Defaults to `<APP>_CACHE_DIR`.
    :return: Cache directory. It is not created and can be absent.
    """
    return _override(env_var or _default_var(app, "CACHE")) or Path(user_cache_dir(app, DEFAULT_APP_AUTHOR))


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
