"""Enumerate consumers by process location, including versioned addon filenames."""
import re
from pathlib import Path


def consumer_id(name):
    low = name.casefold()
    if re.fullmatch(r'renodx-dlss5(?:[-_.].*)?\.addon64', low): return 'renodx_nr'
    if re.fullmatch(r'deep-fried-chicken(?:[-_.].*)?\.addon64', low): return 'chicken'
    if re.fullmatch(r'renodx-dlss(?:[-_.].*)?\.addon64', low): return 'multipass'
    return None


def inspect_conflicts(game, managed=()):
    consumers, conflicts = [], []
    for mod in game.existing_mods:
        cid = consumer_id(mod.name)
        if cid: consumers.append(cid)
        if mod.kind == 'unknown_proxy': conflicts.append('Unidentified proxy: '+mod.name)
    if any(m.kind == 'optiscaler_nr_forwarder' for m in game.existing_mods): consumers.append('optiscaler_nr')
    # Preserve duplicates: ReShade loads two differently versioned files as two addons.
    if len(consumers)>1: conflicts.append('Multiple overlapping neural consumers: '+', '.join(consumers))
    return consumers, conflicts
