from __future__ import annotations

import unittest
from xml.etree import ElementTree as ElementTree

from tests.test_denon import RECORDED_X2100W_STATUS_XML
from tests.test_upnp import FakeUpnpRenderer
from tests.test_yamaha_ync import FakeYamahaYNC
from avr_control import xmltree
from avr_control.drivers.upnp import SERVICE_TYPE, SOAP_NS, _soap_request


def _structure(element):
    return (
        element.tag,
        dict(element.attrib),
        element.text,
        element.tail,
        [_structure(child) for child in element],
    )


class XmlTreeTests(unittest.TestCase):
    def assert_parity(self, payload):
        self.assertEqual(
            _structure(xmltree.fromstring(payload)),
            _structure(ElementTree.fromstring(payload)),
        )

    def test_parses_repository_xml_fixtures_like_elementtree(self):
        description = FakeUpnpRenderer(
            nested=True, multiple_nested=True
        ).description("http://receiver.example/")
        yamaha_status = FakeYamahaYNC()._status_xml()

        for payload in (
            description,
            RECORDED_X2100W_STATUS_XML,
            yamaha_status,
        ):
            with self.subTest(payload=payload[:80]):
                self.assert_parity(payload)

    def test_elementtree_path_queries(self):
        payload = (
            '<root xmlns="urn:device"><device><name>Living</name>'
            '<serviceList><service><serviceType>rendering</serviceType>'
            "</service></serviceList></device>"
            "<device><name>Kitchen</name></device></root>"
        )
        ours = xmltree.fromstring(payload)
        reference = ElementTree.fromstring(payload)
        namespace = "{urn:device}"
        paths = (
            f"{namespace}device",
            f"{namespace}device/{namespace}name",
            f".//{namespace}service",
            ".//{*}name",
            "*",
        )
        for path in paths:
            with self.subTest(path=path):
                self.assertEqual(
                    [_structure(node) for node in ours.findall(path)],
                    [_structure(node) for node in reference.findall(path)],
                )
                self.assertEqual(
                    _structure(ours.find(path)) if ours.find(path) is not None else None,
                    _structure(reference.find(path))
                    if reference.find(path) is not None
                    else None,
                )
        self.assertEqual(
            ours.findtext(f".//{namespace}name"),
            reference.findtext(f".//{namespace}name"),
        )

    def test_preserves_empty_text_whitespace_and_entity_decoding(self):
        payload = b"<root><empty/><space>  A &amp; B \n</space>before<child/>after</root>"
        self.assert_parity(payload)
        root = xmltree.fromstring(payload)
        self.assertIsNone(root.find("empty").text)
        self.assertEqual(root.find("space").text, "  A & B \n")
        self.assertIsNone(root.text)
        self.assertEqual(root[0].tail, None)
        self.assertEqual(root[1].tail, "before")
        self.assertEqual(root[2].tail, "after")

    def test_serializes_soap_and_escaped_content_compatibly(self):
        payload = _soap_request(
            SERVICE_TYPE,
            "SetVolume",
            {"InstanceID": "0", "DesiredVolume": 'A&B < "value"'},
        )
        self.assert_parity(payload)
        parsed = ElementTree.fromstring(payload)
        self.assertEqual(parsed.tag, f"{{{SOAP_NS}}}Envelope")
        self.assertEqual(
            parsed.findtext(".//DesiredVolume"),
            'A&B < "value"',
        )

        root = xmltree.Element("root", {"quote": '"&<', "line": "a\nb"})
        xmltree.SubElement(root, "text").text = "A&B < C > D"
        self.assert_parity(xmltree.tostring(root, encoding="utf-8"))

    def test_rejects_malformed_and_doctype_xml(self):
        for payload in (b"<root><broken></root>", b"not xml"):
            with self.subTest(payload=payload), self.assertRaises(xmltree.ParseError):
                xmltree.fromstring(payload)

        payload = (
            b'<!DOCTYPE root [<!ENTITY x "expanded">]>'
            b"<root>&x;</root>"
        )
        with self.assertRaises(xmltree.ParseError):
            xmltree.fromstring(payload)


if __name__ == "__main__":
    unittest.main()
