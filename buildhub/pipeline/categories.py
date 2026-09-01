"""Mapping each site's own taxonomy onto our browse tree.

Sites disagree about categories entirely -- 1688 says "数码配件 > 蓝牙耳机",
Alibaba says "Consumer Electronics > Earphone & Headphone", Banggood says
"Electronics > Audio". We resolve a product's category by scoring the English
category breadcrumb *and* the product title against a keyword table per leaf
category, then caching the decision per (site, raw_path) in ``site_category_map``
so the same breadcrumb is never re-resolved.

The keyword table is deliberately plain data -- extend it and re-run
``python -m buildhub.cli recategorize`` to reclassify the catalog.
"""

from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db.models import Category, CanonicalProduct, Offer, SiteCategoryMap
from ..util.text import clean, normalize_title

log = logging.getLogger(__name__)

# leaf-category slug -> discriminating keywords
# Electronics + 3D-printing/RepRap taxonomy -- keep in sync with db/seed.py's
# TAXONOMY (slugs are what python-slugify() produces from each category name).
CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "resistors": ["resistor", "resistor kit", "smd resistor"],
    "capacitors": ["capacitor", "ceramic capacitor", "electrolytic capacitor", "tantalum capacitor"],
    "inductors": ["inductor", "choke coil", "toroid inductor"],
    "crystals-oscillators": ["crystal oscillator", "resonator", "quartz crystal"],
    "fuses-ptcs": ["fuse", "ptc fuse", "resettable fuse", "polyfuse"],
    "potentiometers-trimmers": ["potentiometer", "trimmer pot", "rotary pot"],

    "microcontrollers": ["microcontroller", "mcu chip", "atmega", "pic microcontroller"],
    "diodes-rectifiers": ["diode", "rectifier", "schottky", "zener"],
    "transistors-mosfets": ["transistor", "mosfet", "bjt transistor", "igbt"],
    "voltage-regulators": ["voltage regulator", "ldo regulator", "buck regulator ic"],
    "op-amps-analog-ics": ["op-amp", "op amp", "analog ic", "comparator ic"],
    "logic-ics": ["logic ic", "logic gate", "shift register", "multiplexer ic"],
    "sensors-digital": ["digital sensor", "i2c sensor", "spi sensor"],
    "memory-storage-ics": ["eeprom", "flash memory ic", "sram chip"],

    "esp32-esp8266": ["esp32", "esp8266", "wemos d1"],
    "arduino-avr": ["arduino", "avr board", "atmega328"],
    "rp2040-pi-pico": ["rp2040", "pi pico", "raspberry pi pico"],
    "stm32": ["stm32", "bluepill", "blackpill"],
    "raspberry-pi": ["raspberry pi", "raspberry pi 4", "raspberry pi 5", "raspberry pi zero"],
    "relay-driver-modules": ["relay module", "motor driver module", "l298n", "relay board"],
    "rf-wireless-modules": ["nrf24", "lora module", "rf module", "bluetooth module", "wifi module"],
    "sensor-breakout-boards": ["breakout board", "sensor module", "bme280", "dht22", "mpu6050"],

    "li-ion-lipo-batteries": ["18650", "lipo battery", "li-ion battery", "lithium battery cell"],
    "battery-management-bms-chargers": ["bms board", "battery charger module", "tp4056", "battery management"],
    "dc-dc-converters": ["dc-dc converter", "buck converter", "boost converter"],
    "ac-dc-power-supplies": ["power supply", "psu", "smps", "ac-dc adapter"],
    "solar-panels": ["solar panel", "solar cell"],
    "power-connectors": ["dc jack", "power connector", "barrel connector", "xt60", "xt30"],
    "fans-cooling": ["cooling fan", "heatsink", "blower fan"],

    "headers-terminal-blocks": ["header pin", "terminal block", "pin header", "screw terminal"],
    "jst-molex-xt-connectors": ["jst connector", "molex connector", "xt connector", "dupont connector"],
    "hookup-silicone-wire": ["silicone wire", "hookup wire", "22awg wire", "26awg wire"],
    "ribbon-zif-cable": ["ribbon cable", "zif connector", "flex cable"],
    "crimp-terminals": ["crimp terminal", "ferrule", "crimp connector"],
    "breadboards-prototyping": ["breadboard", "perfboard", "prototype board"],

    "oled-lcd-displays": ["oled display", "lcd display", "ssd1306"],
    "tft-touch-displays": ["tft display", "touch display", "ips display"],
    "led-indicators-strips": ["led strip", "ws2812", "led indicator", "neopixel"],
    "buttons-switches-encoders": ["push button", "toggle switch", "rotary encoder", "limit switch"],
    "keypads": ["keypad", "membrane keypad"],

    "extrusion-profiles-2020-2040": ["2020 extrusion", "2040 extrusion", "aluminum extrusion", "v-slot"],
    "linear-rails-bearings": ["mgn12", "linear rail", "lm8uu", "linear bearing"],
    "lead-screws-rods": ["lead screw", "smooth rod", "trapezoidal screw"],
    "gt2-belts-pulleys": ["gt2 belt", "gt2 pulley", "timing belt"],
    "corner-brackets-plates": ["corner bracket", "gusset plate", "l bracket"],
    "vibration-dampers": ["vibration damper", "anti-vibration mount"],

    "stepper-motors-nema": ["nema17", "nema14", "nema23", "stepper motor"],
    "stepper-drivers": ["a4988", "drv8825", "tmc2209", "stepper driver"],
    "mainboards": ["3d printer mainboard", "skr mini", "printer control board"],
    "endstops-probes-bltouch-etc": ["bltouch", "endstop", "limit switch probe", "cr touch"],
    "cooling-fans-ducts": ["hotend fan", "part cooling fan", "fan duct"],

    "hotends-heater-blocks": ["hotend", "heater block", "volcano hotend"],
    "nozzles": ["mk8 nozzle", "brass nozzle", "hardened steel nozzle"],
    "heater-cartridges-thermistors": ["heater cartridge", "thermistor", "ntc 100k"],
    "extruders-direct-bowden": ["bmg extruder", "bowden extruder", "direct drive extruder"],
    "ptfe-tubing-fittings": ["ptfe tube", "bowden tubing", "pneumatic fitting"],
    "heated-beds-build-plates": ["heated bed", "build plate", "pei sheet", "glass bed"],

    "pla": ["pla filament"],
    "petg": ["petg filament"],
    "abs-asa": ["abs filament", "asa filament"],
    "tpu-flexible": ["tpu filament", "flexible filament"],
    "nylon-composites": ["nylon filament", "carbon fiber filament", "composite filament"],
    "resin-sla-msla": ["resin", "sla resin", "msla resin"],
    "support-specialty-filament": ["support filament", "dissolvable filament", "specialty filament"],

    "soldering-rework": ["soldering iron", "solder", "hot air rework", "flux", "desolder"],
    "measuring-test-equipment": ["multimeter", "caliper", "oscilloscope"],
    "hand-tools": ["screwdriver", "wrench", "plier", "hex key"],
    "hardware-screws-nuts-inserts": ["m3 screw", "heat set insert", "t-nut", "socket head screw"],
    "adhesives-tapes": ["super glue", "kapton tape", "glue stick"],
    "workshop-storage": ["parts bin", "component organizer", "storage case"],
    "safety-ppe": ["safety glasses", "nitrile gloves", "respirator mask"],

    "project-boxes-enclosures": ["project box", "enclosure", "junction box"],
    "pcb-fabrication-prototyping": ["pcb", "protoboard", "custom pcb"],
    "3d-printed-mounts-brackets": ["printed mount", "printed bracket"],
    "panel-mount-hardware": ["panel mount", "din rail"],
}

# Coarse fallbacks when nothing leaf-level hits.
TOP_LEVEL_HINTS = {
    "passives": ["passive component", "元器件"],
    "semiconductors": ["semiconductor", "ic chip", "芯片"],
    "boards": ["dev board", "development board", "开发板"],
    "power": ["power supply", "battery", "电源"],
    "connectors": ["connector", "wiring", "接线"],
    "displays": ["display", "screen module", "显示屏"],
    "printer_frame": ["3d printer frame", "3d printer motion"],
    "printer_motion_electronics": ["3d printer electronics", "printer mainboard"],
    "hotend": ["hotend", "extruder", "3d打印机"],
    "filament": ["filament", "3d printing material"],
    "tools": ["tool", "workshop", "工具"],
    "enclosures": ["enclosure", "prototyping"],
}


class Categorizer:
    def __init__(self, session: Session):
        self.session = session
        self._by_slug: dict[str, Category] = {
            c.slug: c for c in session.scalars(select(Category)).all()
        }
        self._cache: dict[tuple[int, str], Optional[int]] = {}

    def resolve(
        self, *, site_id: int, raw_path: str | None, title: str | None
    ) -> Optional[Category]:
        """Best category for a listing, using its breadcrumb then its title."""
        raw_path = clean(raw_path or "")
        cache_key = (site_id, raw_path)

        if raw_path and cache_key in self._cache:
            cid = self._cache[cache_key]
            return self.session.get(Category, cid) if cid else None

        mapped = None
        if raw_path:
            mapped = self.session.scalar(
                select(SiteCategoryMap).where(
                    SiteCategoryMap.site_id == site_id,
                    SiteCategoryMap.raw_path == raw_path,
                )
            )
            if mapped is not None:
                mapped.hits += 1
                if mapped.category_id:
                    self._cache[cache_key] = mapped.category_id
                    return self.session.get(Category, mapped.category_id)
                # A row already exists for this (site, raw_path) but was previously
                # unresolved -- update it in place. Falling through to add() here
                # would insert a second row for the same key and violate the unique
                # constraint the very next time the same breadcrumb comes through.

        haystack = normalize_title(f"{raw_path} {raw_path} {title or ''}")
        category, confidence = self._score(haystack)

        if raw_path:
            if mapped is not None:
                mapped.category_id = category.id if category else None
                mapped.confidence = confidence
            else:
                self.session.add(
                    SiteCategoryMap(
                        site_id=site_id,
                        raw_path=raw_path[:512],
                        raw_path_en=raw_path[:512],
                        category_id=category.id if category else None,
                        confidence=confidence,
                        hits=1,
                    )
                )
            self._cache[cache_key] = category.id if category else None

        return category

    def _score(self, haystack: str) -> tuple[Optional[Category], float]:
        if not haystack:
            return None, 0.0

        best_slug, best_score = None, 0.0
        for slug, keywords in CATEGORY_KEYWORDS.items():
            if slug not in self._by_slug:
                continue
            score = 0.0
            for kw in keywords:
                if kw in haystack:
                    # Longer phrases are far more specific than single words.
                    score += 1.0 + 0.35 * kw.count(" ")
            if score > best_score:
                best_slug, best_score = slug, score

        if best_slug and best_score >= 1.0:
            return self._by_slug[best_slug], min(1.0, best_score / 3.0)

        for slug, hints in TOP_LEVEL_HINTS.items():
            if slug in self._by_slug and any(h in haystack for h in hints):
                return self._by_slug[slug], 0.35

        return None, 0.0


def recount_categories(session: Session) -> None:
    """Refresh the product counters shown in the browse sidebar.

    A parent's count includes its whole subtree, which is what a user browsing
    "Consumer Electronics" expects to see.
    """
    counts = dict(
        session.execute(
            select(CanonicalProduct.category_id, func.count(CanonicalProduct.id))
            .where(CanonicalProduct.is_active.is_(True))
            .group_by(CanonicalProduct.category_id)
        ).all()
    )

    categories = session.scalars(select(Category)).all()
    by_id = {c.id: c for c in categories}

    for c in categories:
        c.product_count = int(counts.get(c.id, 0) or 0)

    for c in categories:
        if c.parent_id and c.product_count:
            parent = by_id.get(c.parent_id)
            while parent is not None:
                parent.product_count += c.product_count
                parent = by_id.get(parent.parent_id) if parent.parent_id else None


def recategorize_all(session: Session, batch: int = 500) -> int:
    """Re-run classification over every product. Use after editing the keyword table."""
    cat = Categorizer(session)
    changed = 0
    offset = 0
    while True:
        products = session.scalars(
            select(CanonicalProduct).order_by(CanonicalProduct.id).limit(batch).offset(offset)
        ).all()
        if not products:
            break
        offset += batch
        for product in products:
            offer = session.scalar(
                select(Offer).where(Offer.canonical_id == product.id).limit(1)
            )
            resolved = cat.resolve(
                site_id=offer.site_id if offer else 0,
                raw_path=offer.raw_category_path if offer else None,
                title=product.title_en,
            )
            if resolved and resolved.id != product.category_id:
                product.category_id = resolved.id
                changed += 1
        session.flush()
    recount_categories(session)
    return changed
