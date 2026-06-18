"""Service to look up a triggered rule inside enabled pack zip archives."""

import asyncio
import logging
import re
import zipfile
from pathlib import Path

import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.device import Device
from app.models.device_group import DeviceGroup
from app.models.pack_enabled import PackEnabled
from app.models.pack_version_rule import PackVersionRule


def _rule_path_in_zip(rule_type: str, rule_id: str) -> list[str]:
    """Return candidate paths inside the zip for the given rule_id and rule_type."""
    # rule_id may already contain a path like "sigma/my_rule.yml" or just "my_rule"
    # We normalise and build lookup candidates.
    candidates: list[str] = []

    if rule_type == "sigma":
        # Accept with or without extension, with or without directory prefix
        for ext in ("", ".yml", ".yaml"):
            name = rule_id if rule_id.startswith("sigma/") else f"sigma/{rule_id}"
            if ext and not name.endswith(ext):
                candidates.append(name + ext)
            else:
                candidates.append(name)
        # Also try bare name inside sigma/
        bare = Path(rule_id).name
        for ext in ("", ".yml", ".yaml"):
            candidates.append(f"sigma/{bare}{ext}")
    elif rule_type == "yara":
        for ext in ("", ".yar"):
            name = rule_id if rule_id.startswith("yara/") else f"yara/{rule_id}"
            if ext and not name.endswith(ext):
                candidates.append(name + ext)
            else:
                candidates.append(name)
        bare = Path(rule_id).name
        for ext in ("", ".yar"):
            candidates.append(f"yara/{bare}{ext}")
    elif rule_type == "ioc":
        if "::" in rule_id:
            ioc_type = rule_id.split("::")[0].lower()
            if ioc_type == "hash":
                candidates.append("ioc/hashes.txt")
            else:
                candidates.append(f"ioc/{ioc_type}s.txt")

    # Deduplicate while preserving order
    seen: set[str] = set()
    result: list[str] = []
    for c in candidates:
        if c not in seen:
            seen.add(c)
            result.append(c)
    return result


def _find_rule_in_zip(zip_path: str, rule_type: str, rule_id: str) -> str | None:
    """Open a pack zip and return the content of the matching rule file, or None."""
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            names = set(zf.namelist())

            if rule_type == "sigma":
                for name in names:
                    if name.startswith("sigma/") and (name.endswith(".yml") or name.endswith(".yaml")):
                        try:
                            with zf.open(name) as f:
                                content = f.read().decode("utf-8", errors="replace")
                                parsed = yaml.safe_load(content)
                                if isinstance(parsed, dict) and str(parsed.get("id")) == str(rule_id):
                                    return content
                        except Exception as e:
                            logging.getLogger(__name__).debug("Error parsing sigma rule in %s: %s", name, e)
                            continue
            elif rule_type == "yara":
                pattern = re.compile(rf"\brule\s+{re.escape(rule_id)}\b")
                for name in names:
                    if name.startswith("yara/") and name.endswith(".yar"):
                        try:
                            with zf.open(name) as f:
                                content = f.read().decode("utf-8", errors="replace")
                                if pattern.search(content):
                                    return content
                        except Exception as e:
                            logging.getLogger(__name__).debug("Error parsing yara rule in %s: %s", name, e)
                            continue
            else:
                for candidate in _rule_path_in_zip(rule_type, rule_id):
                    if candidate in names:
                        with zf.open(candidate) as f:
                            return f.read().decode("utf-8", errors="replace")
    except Exception as exc:
        logging.getLogger(__name__).debug("Error reading zip %s: %s", zip_path, exc)
    return None


async def find_and_cache_triggered_rule(
    device: Device,
    rule_id: str,
    rule_type: str,
    db: AsyncSession,
) -> PackVersionRule | None:
    """
    Search all enabled packs for the device for the given rule.  If found, upsert a row
    in ``pack_version_rules`` and return it.  Returns None if the rule cannot be located.
    """
    # Load device groups with their enabled packs
    result = await db.execute(
        select(Device)
        .options(selectinload(Device.groups).selectinload(DeviceGroup.packs).selectinload(PackEnabled.pack_version))
        .where(Device.id == device.id)
    )
    dev = result.scalar_one_or_none()
    if dev is None:
        return None

    # Collect unique (pack_version_id, zip_path) pairs enabled for this device
    seen_pv_ids: set[int] = set()
    pack_version_paths: list[tuple[int, str]] = []
    for group in dev.groups:
        for pe in group.packs:
            pv = pe.pack_version
            if pv.id not in seen_pv_ids:
                seen_pv_ids.add(pv.id)
                pack_version_paths.append((pv.id, pv.zip_path))

    def _scan_all_zips() -> tuple[int, str] | None:
        """Blocking: scan each pack zip in order; return (pack_version_id, content) if found."""
        for pv_id, zip_path in pack_version_paths:
            content = _find_rule_in_zip(zip_path, rule_type, rule_id)
            if content is not None:
                return pv_id, content
        return None

    found = await asyncio.to_thread(_scan_all_zips)
    if found is None:
        return None

    pv_id, content = found

    # Upsert: check if we already have this cached
    existing = await db.execute(
        select(PackVersionRule).where(
            PackVersionRule.rule_id == rule_id,
            PackVersionRule.rule_type == rule_type,
            PackVersionRule.pack_version_id == pv_id,
        )
    )
    pack_version_rule = existing.scalar_one_or_none()
    if pack_version_rule is None:
        pack_version_rule = PackVersionRule(
            rule_id=rule_id,
            rule_type=rule_type,
            pack_version_id=pv_id,
            rule_content=content,
        )
        db.add(pack_version_rule)
        await db.flush()  # populate id before returning

    return pack_version_rule
