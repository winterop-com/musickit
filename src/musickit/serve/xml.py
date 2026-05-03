"""JSON → Subsonic-XML converter.

The Subsonic spec specifies XML as the default response format; JSON is
opt-in via `?f=json`. We build every endpoint's response as a dict (the
JSON shape) and let `to_xml` translate when the client wants XML.

Mapping rules — derived from the Subsonic XSD and reference impl:

- Scalar dict values → element attributes
- Dict-valued keys     → child elements (recursively)
- List-valued keys     → repeated child elements with the list's key as
                         the element name (Subsonic JSON keeps lists
                         already singular-named, mirroring the XML)
- `None` values        → omitted
- `bool` values        → "true" / "false"
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any

_XMLNS = "http://subsonic.org/restapi"


def to_xml(payload: dict[str, Any]) -> bytes:
    """Convert a Subsonic envelope dict to its XML form (UTF-8 bytes with declaration)."""
    inner = payload.get("subsonic-response", {})
    root = ET.Element("subsonic-response", {"xmlns": _XMLNS})
    _populate(root, inner)
    body: bytes = ET.tostring(root, encoding="utf-8")
    return b'<?xml version="1.0" encoding="UTF-8"?>\n' + body


def _populate(elem: ET.Element, data: Any) -> None:
    if not isinstance(data, dict):
        return
    for key, value in data.items():
        if value is None:
            continue
        if isinstance(value, list):
            for item in value:
                child = ET.SubElement(elem, key)
                if isinstance(item, dict):
                    _populate(child, item)
                else:
                    child.text = _xml_str(item)
        elif isinstance(value, dict):
            child = ET.SubElement(elem, key)
            _populate(child, value)
        else:
            elem.set(key, _xml_str(value))


def _xml_str(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)
