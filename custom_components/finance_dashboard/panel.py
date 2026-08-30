"""Sidebar panel registration for Finance — uses ha-customapps PanelRegistrar."""

from __future__ import annotations

import logging
from pathlib import Path

from ha_customapps.panel import PanelRegistrar
from homeassistant.core import HomeAssistant

from .const import (
    DOMAIN,
    PANEL_COMPONENT_NAME,
    PANEL_ICON,
    PANEL_TITLE,
    PANEL_URL_PATH,
    VERSION,
)

_LOGGER = logging.getLogger(__name__)

STATIC_BASE = f"/api/{DOMAIN}/static"
LOVELACE_COMPONENTS = [
    f"{STATIC_BASE}/fd-shared-styles.js",
    f"{STATIC_BASE}/fd-data-provider.js",
    f"{STATIC_BASE}/fd-stat-card.js",
    f"{STATIC_BASE}/fd-person-card.js",
    f"{STATIC_BASE}/fd-donut-chart.js",
    f"{STATIC_BASE}/fd-header.js",
    f"{STATIC_BASE}/fd-stats-row.js",
    f"{STATIC_BASE}/fd-household-section.js",
    f"{STATIC_BASE}/fd-budget-plan.js",
    f"{STATIC_BASE}/fd-category-section.js",
    f"{STATIC_BASE}/fd-cost-distribution.js",
    f"{STATIC_BASE}/fd-recurring-list.js",
    f"{STATIC_BASE}/fd-transactions-log.js",
    f"{STATIC_BASE}/fd-budget-config.js",
    f"{STATIC_BASE}/fd-categorize.js",
    f"{STATIC_BASE}/fd-setup-wizard.js",
    f"{STATIC_BASE}/finance-status-chip.js",
]


def _asset_stamp(frontend_dir: str) -> str:
    """Return a cache key that changes whenever a panel asset changes.

    The assets are served with a long cache lifetime, so a plain URL keeps
    the browser on the old bundle after an update — for as long as the cache
    entry lives.  The newest mtime in the frontend directory is enough to
    make every URL unique per build, including local edits that do not bump
    VERSION.  Called from the executor: it touches the filesystem.
    """
    try:
        newest = max(
            (p.stat().st_mtime_ns for p in Path(frontend_dir).glob("*.js")),
            default=0,
        )
    except OSError:
        return VERSION
    return f"{VERSION}-{newest // 1_000_000_000}"


async def async_register_panel(hass: HomeAssistant) -> None:
    """Register the Finance sidebar panel.

    Any panel left over from an earlier setup of this entry is removed
    first: ``frontend.async_register_built_in_panel`` refuses to overwrite
    an existing URL path, so a second registration (config-entry reload,
    or setup running twice in one start) would otherwise raise
    ``ValueError: Overwriting panel``.
    """
    await async_unregister_panel(hass)
    frontend_dir = hass.config.path("custom_components", DOMAIN, "frontend")
    stamp = await hass.async_add_executor_job(_asset_stamp, frontend_dir)
    lovelace_urls = [f"{url}?v={stamp}" for url in LOVELACE_COMPONENTS]
    try:
        registrar = PanelRegistrar(
            hass=hass,
            domain=DOMAIN,
            panel_component=PANEL_COMPONENT_NAME,
            panel_title=PANEL_TITLE,
            panel_icon=PANEL_ICON,
            panel_url_path=PANEL_URL_PATH,
            module_url=f"{STATIC_BASE}/finance-dashboard-panel.js?v={stamp}",
            frontend_dir=frontend_dir,
            lovelace_urls=lovelace_urls,
        )
        await registrar.async_register()
        _LOGGER.debug("Finance panel registered at /%s", PANEL_URL_PATH)
    except Exception:
        _LOGGER.exception("Failed to register Finance panel")


async def async_unregister_panel(hass: HomeAssistant) -> None:
    """Unregister custom sidebar panel.

    ``frontend.async_remove_panel`` is the supported way to drop a panel;
    ``panel_custom`` exposes no unregister helper, so the previous call
    raised AttributeError, was swallowed, and left the panel registered —
    which then made the next registration fail with "Overwriting panel".
    """
    from homeassistant.components import frontend

    try:
        frontend.async_remove_panel(hass, PANEL_URL_PATH, warn_if_unknown=False)
    except Exception:
        _LOGGER.debug("Panel was not registered, nothing to remove")
