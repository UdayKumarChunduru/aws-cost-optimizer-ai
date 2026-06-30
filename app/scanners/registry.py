"""
Scanner Registry - Plugin Architecture
Previously, adding a new scanner required editing main.py AND
weekly_report.py by hand to register it in a hardcoded dict/list,
which doesn't scale and is easy to forget when adding the 11th,
12th, etc. scanner.

Each scanner module must expose:
  - SCANNER_NAME: str           (unique key, e.g. "ec2_idle")
  - SCANNER_LABEL: str          (human-readable, e.g. "EC2 Idle Instances")
  - scan_region(region) -> list[dict]   (runs against one region)

This file auto-discovers every module in app/scanners/ that defines
those three things, with NO manual import list to maintain. Drop a
new file in this folder following the contract and it's picked up
automatically next run - no other file needs to change.
"""
import importlib
import logging
import pkgutil
from dataclasses import dataclass
from typing import Callable

logger = logging.getLogger(__name__)

REQUIRED_ATTRS = ("SCANNER_NAME", "SCANNER_LABEL", "scan_region")


@dataclass
class ScannerPlugin:
    name: str
    label: str
    scan_region_fn: Callable


def discover_scanners() -> dict[str, ScannerPlugin]:
    """
    Walks the app.scanners package, imports every submodule, and
    registers any module that implements the scanner contract.
    Modules missing any required attribute are skipped with a warning
    rather than crashing the whole registry - one broken scanner file
    should never take down the other nine.
    """
    import app.scanners as scanners_pkg

    plugins: dict[str, ScannerPlugin] = {}

    # Infrastructure modules in this package that are NOT scanners and
    # must never be auto-imported as one. Listed explicitly rather than
    # relying on import failures to skip them silently - orchestrator.py
    # imports this registry module, so importing it here would be a
    # circular import. Real scanner files never need to import the
    # registry, so this exclusion list should never need a 3rd entry.
    NON_SCANNER_MODULES = ("registry", "orchestrator", "__init__")

    for module_info in pkgutil.iter_modules(scanners_pkg.__path__):
        module_name = module_info.name
        if module_name in NON_SCANNER_MODULES:
            continue
        try:
            module = importlib.import_module(f"app.scanners.{module_name}")
        except Exception as exc:
            logger.error("Failed to import scanner module %s: %s", module_name, exc)
            continue

        missing = [attr for attr in REQUIRED_ATTRS if not hasattr(module, attr)]
        if missing:
            logger.warning(
                "Skipping %s - missing required attributes: %s", module_name, missing
            )
            continue

        plugin = ScannerPlugin(
            name=module.SCANNER_NAME,
            label=module.SCANNER_LABEL,
            scan_region_fn=module.scan_region,
        )
        if plugin.name in plugins:
            logger.error(
                "Duplicate scanner name '%s' from module %s - skipping", plugin.name, module_name
            )
            continue
        plugins[plugin.name] = plugin
        logger.info("Registered scanner: %s (%s)", plugin.name, plugin.label)

    return plugins


# Discovered once at import time. In a long-lived process (FastAPI) this
# means new scanner files require a restart to be picked up, which is
# expected and matches how Python module imports normally work.
_REGISTRY = discover_scanners()


def get_all_scanners() -> dict[str, ScannerPlugin]:
    return _REGISTRY


def get_scanner(name: str) -> ScannerPlugin | None:
    return _REGISTRY.get(name)
