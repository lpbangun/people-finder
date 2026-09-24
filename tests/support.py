"""Small host-independent helpers for the repository's test harnesses."""

import os
import sys


def resolve_jobsss_bin(plugin_root, environ=None):
    """Resolve the optional JobSSS executable without assuming a user checkout path.

    JOBSSS_BIN may be absolute or relative to the People Finder repository root.
    Without it, use the conventional sibling checkout layout.
    """
    env = os.environ if environ is None else environ
    configured = env.get("JOBSSS_BIN")
    if configured:
        configured = os.path.expandvars(os.path.expanduser(configured))
        if not os.path.isabs(configured):
            configured = os.path.join(plugin_root, configured)
        return os.path.abspath(configured)
    return os.path.abspath(os.path.join(plugin_root, "..", "jobsss", "bin", "jobsss"))


def portable_product_argv(argv, *, python_executable=None):
    """Run the extensionless People Finder Python entry point portably.

    Agent Plugin packages keep bin/people-finder extensionless for Unix hosts.
    Test harnesses use this helper so Windows does not need to execute a Unix
    shebang file directly. The basename rule also covers relocated installs.
    """
    values = [str(value) for value in argv]
    if not values:
        return values
    launcher = os.path.normcase(os.path.basename(values[0]))
    parent = os.path.normcase(os.path.basename(os.path.dirname(values[0])))
    if launcher != "people-finder" or parent != "bin":
        return values
    return [python_executable or sys.executable, values[0], *values[1:]]
