"""CryEngine's binary XML, read back into ElementTree.

Star Citizen stores most of its XML configuration - defaultProfile.xml among
them - as "CryXmlB": a header, a table of nodes, a table of attributes, a
table of child indices, and one string table that every name, value and
text refers into by offset. Nothing is compressed or encrypted at this
layer; it is XML with the parsing already done.

Layout, little-endian throughout:

    header      "CryXmlB\\0", then nine uint32: total size, node table
                offset, node count, attribute table offset, attribute
                count, child table offset, child count, string table
                offset, string table size
    node        28 bytes: name offset, content offset, attribute count
                (uint16), child count (uint16), parent index, first
                attribute index, first child index, reserved
    attribute   8 bytes: name offset, value offset
    child table uint32 node indices, in the order the children appear

Offsets into the string table point at NUL-terminated UTF-8.
"""

from __future__ import annotations

import struct
import xml.etree.ElementTree as ET

SIGNATURE = b"CryXmlB\0"
_HEADER = struct.Struct("<8s9I")
_NODE = struct.Struct("<IIHHIIII")
_ATTRIBUTE = struct.Struct("<II")


def is_cryxml(data: bytes) -> bool:
    return data[:8] == SIGNATURE


def parse(data: bytes) -> ET.Element:
    """The document's root element, or ValueError if this is not CryXmlB."""
    if not is_cryxml(data):
        raise ValueError("not CryXmlB")
    (_sig, total, node_off, node_count, attr_off, attr_count,
     child_off, child_count, str_off, str_size) = _HEADER.unpack_from(data, 0)
    if total != len(data):
        raise ValueError("CryXmlB says %d bytes, got %d" % (total, len(data)))
    strings = data[str_off:str_off + str_size]

    def string(offset: int) -> str:
        end = strings.index(b"\0", offset)
        return strings[offset:end].decode("utf-8", "replace")

    attributes = [_ATTRIBUTE.unpack_from(data, attr_off + i * _ATTRIBUTE.size)
                  for i in range(attr_count)]
    children = struct.unpack_from("<%dI" % child_count, data, child_off)

    elements: list[ET.Element] = []
    for i in range(node_count):
        (name_off, content_off, n_attr, n_child, _parent,
         first_attr, first_child, _reserved) = _NODE.unpack_from(data, node_off + i * _NODE.size)
        element = ET.Element(string(name_off))
        for a in range(first_attr, first_attr + n_attr):
            key_off, value_off = attributes[a]
            element.set(string(key_off), string(value_off))
        text = string(content_off)
        if text:
            element.text = text
        elements.append((element, first_child, n_child))
    for element, first_child, n_child in elements:
        for c in children[first_child:first_child + n_child]:
            element.append(elements[c][0])
    return elements[0][0]


def to_text(data: bytes) -> str:
    """The document as ordinary XML, for reading with the eye."""
    root = parse(data)
    _indent(root)
    return ET.tostring(root, encoding="unicode")


def _indent(element: ET.Element, level: int = 0) -> None:
    pad = "\n" + "  " * level
    if len(element):
        if not (element.text or "").strip():
            element.text = pad + "  "
        for child in element:
            _indent(child, level + 1)
        if not (child.tail or "").strip():
            child.tail = pad
    if level and not (element.tail or "").strip():
        element.tail = pad
