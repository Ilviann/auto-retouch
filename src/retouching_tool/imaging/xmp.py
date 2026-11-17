"""Preserve standard JPEG XMP and append one Adobe XMP Media Management event."""

import json
from xml.dom import Node, minidom
from xml.parsers import expat

from .exif import MAX_APP_PAYLOAD

XMP_HEADER = b"http://ns.adobe.com/xap/1.0/\0"
EXTENDED_XMP_HEADER = b"http://ns.adobe.com/xmp/extension/\0"
RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
XMLNS = "http://www.w3.org/2000/xmlns/"
MM = "http://ns.adobe.com/xap/1.0/mm/"
EVENT = "http://ns.adobe.com/xap/1.0/sType/ResourceEvent#"
TIFF = "http://ns.adobe.com/tiff/1.0/"
EXIF = "http://ns.adobe.com/exif/1.0/"
XAP = "http://ns.adobe.com/xap/1.0/"
NOTE = "http://ns.adobe.com/xmp/note/"
PHOTOSHOP = "http://ns.adobe.com/photoshop/1.0/"
EMPTY_XMP = f'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="{RDF}"/></x:xmpmeta>'.encode()


def _elements(node):
    return [child for child in node.childNodes if child.nodeType == Node.ELEMENT_NODE]


def _text(node):
    if any(child.nodeType not in (Node.TEXT_NODE, Node.CDATA_SECTION_NODE) for child in node.childNodes):
        raise ValueError(f"Unexpected structured XMP value for {node.localName}")
    return "".join(child.data for child in node.childNodes)


def _value(node):
    return node.value if node.nodeType == Node.ATTRIBUTE_NODE else _text(node)


class Xmp:
    def __init__(self, raw):
        payload = raw or EMPTY_XMP
        if len(payload) + len(XMP_HEADER) > MAX_APP_PAYLOAD:
            raise ValueError("XMP exceeds one JPEG APP1 segment")

        # Reject DTDs and excessive nesting before DOM construction/serialization.
        parser = expat.ParserCreate()
        depth = 0

        def start(*_):
            nonlocal depth
            depth += 1
            if depth > 64:
                raise ValueError("XMP nesting exceeds 64 levels")

        def end(*_):
            nonlocal depth
            depth -= 1

        def reject_dtd(*_):
            raise ValueError("DTD declarations in JPEG XMP are unsupported")

        parser.StartElementHandler, parser.EndElementHandler = start, end
        parser.StartDoctypeDeclHandler = reject_dtd
        parser.Parse(payload, True)
        self.document = minidom.parseString(payload)
        graphs = self.document.getElementsByTagNameNS(RDF, "RDF")
        if len(graphs) != 1:
            raise ValueError("JPEG XMP requires one RDF graph")
        self.graph = graphs[0]
        self.primary = [node for node in _elements(self.graph) if node.namespaceURI == RDF
                        and node.localName == "Description" and not node.getAttributeNS(RDF, "about")]
        if self.fields(NOTE, "HasExtendedXMP"):
            raise ValueError("Extended JPEG XMP is unsupported")

    def fields(self, namespace, name):
        values = []
        for node in self.primary:
            attribute = node.getAttributeNodeNS(namespace, name)
            values.extend(child for child in _elements(node) if (child.namespaceURI, child.localName) == (namespace, name))
            if attribute is not None:
                values.append(attribute)
        return values

    def number(self, namespace, name):
        values = {int(_value(field)) for field in self.fields(namespace, name)}
        if len(values) > 1:
            raise ValueError(f"Conflicting JPEG XMP {name} values")
        return next(iter(values), None)

    def _append(self, parent, namespace, qualified_name, *, declare=False):
        node = self.document.createElementNS(namespace, qualified_name)
        if declare:
            node.setAttributeNS(XMLNS, "xmlns:" + qualified_name.split(":")[0], namespace)
        parent.appendChild(node)
        return node

    def for_output(self, size, software, processed_at, pipeline_sha256, previous_software):
        width, height = size
        previous_xmp_software = sorted({_value(node) for node in self.fields(TIFF, "Software")}
                                       - {"", previous_software})
        for namespace, name, value in (
                (TIFF, "Orientation", 1), (TIFF, "ImageWidth", width), (TIFF, "ImageLength", height),
                (TIFF, "Software", software), (EXIF, "PixelXDimension", width),
                (EXIF, "PixelYDimension", height), (EXIF, "ColorSpace", 1), (PHOTOSHOP, "ICCProfile", "sRGB")):
            for node in self.fields(namespace, name):
                if node.nodeType == Node.ATTRIBUTE_NODE:
                    node.value = str(value)
                else:
                    _text(node)  # Refuse to erase structured values.
                    if any(attribute.namespaceURI != XMLNS for attribute in node.attributes.values()):
                        raise ValueError(f"Qualified XMP {name} is unsupported for export")
                    while node.firstChild:
                        node.removeChild(node.firstChild)
                    node.appendChild(self.document.createTextNode(str(value)))
        for namespace, names in (
                (XAP, ("Thumbnails",)),
                (TIFF, ("BitsPerSample", "Compression", "PhotometricInterpretation", "SamplesPerPixel",
                        "PlanarConfiguration", "YCbCrSubSampling", "YCbCrPositioning", "TransferFunction",
                        "WhitePoint", "PrimaryChromaticities", "YCbCrCoefficients", "ReferenceBlackWhite")),
                (EXIF, ("ComponentsConfiguration", "CompressedBitsPerPixel", "Gamma"))):
            for name in names:
                for node in self.fields(namespace, name):
                    if node.nodeType == Node.ATTRIBUTE_NODE:
                        node.ownerElement.removeAttributeNode(node)
                    else:
                        node.parentNode.removeChild(node)

        history = self.fields(MM, "History")
        if history:
            if len(history) != 1 or history[0].nodeType != Node.ELEMENT_NODE:
                raise ValueError("Unsupported JPEG XMP History representation")
            sequences = _elements(history[0])
            if len(sequences) != 1 or (sequences[0].namespaceURI, sequences[0].localName) != (RDF, "Seq"):
                raise ValueError("JPEG XMP History requires an RDF sequence")
            sequence = sequences[0]
        else:
            description = self._append(self.graph, RDF, "rdf:Description", declare=True)
            description.setAttributeNS(RDF, "rdf:about", "")
            sequence = self._append(self._append(description, MM, "xmpMM:History", declare=True), RDF, "rdf:Seq")

        event = self._append(sequence, RDF, "rdf:li", declare=True)
        event.setAttributeNS(RDF, "rdf:parseType", "Resource")
        event.setAttributeNS(XMLNS, "xmlns:stEvt", EVENT)
        parameters = {"pipelineSHA256": pipeline_sha256}
        if previous_software:
            parameters["previousSoftware"] = previous_software
        if previous_xmp_software:
            parameters["previousXmpSoftware"] = previous_xmp_software
        for name, value in (("action", "saved"), ("softwareAgent", software), ("when", processed_at),
                            ("parameters", json.dumps(parameters, separators=(",", ":")))):
            self._append(event, EVENT, "stEvt:" + name).appendChild(self.document.createTextNode(value))
        output = self.document.toxml(encoding="utf-8")
        if len(output) + len(XMP_HEADER) > MAX_APP_PAYLOAD:
            raise ValueError("XMP exceeds one JPEG APP1 segment after adding processing history")
        return output
