"""Small ElementTree-compatible XML subset for Decky's frozen Python runtime."""

from __future__ import annotations

from xml.parsers import expat


class ParseError(SyntaxError):
    """XML syntax error, with the position and code exposed by ElementTree."""

    def __init__(self, message: str, code: int | None = None, position=None):
        super().__init__(message)
        self.code = code
        self.position = position


def _expanded_name(name: str) -> str:
    if "}" in name:
        namespace, local = name.split("}", 1)
        return "{" + namespace + "}" + local
    return name


def _matches(tag: str, wanted: str) -> bool:
    if wanted == "*" or wanted == "{*}*":
        return True
    if wanted.startswith("{*}"):
        return tag.rsplit("}", 1)[-1] == wanted[3:]
    if wanted.endswith("}*") and wanted.startswith("{"):
        return tag.startswith(wanted[:-1])
    return tag == wanted


def _path_name(name: str, namespaces: dict[str, str] | None) -> str:
    if not namespaces or name.startswith("{") or ":" not in name:
        return name
    prefix, local = name.split(":", 1)
    if prefix in namespaces:
        return "{" + namespaces[prefix] + "}" + local
    return name


def _path_steps(path: str, namespaces: dict[str, str] | None):
    if path == ".":
        return []
    steps = []
    index = 0
    axis = "child"
    if path.startswith("./"):
        index = 2
        if path.startswith(".//"):
            index = 3
            axis = "descendant"
    while index < len(path):
        start = index
        in_namespace = False
        while index < len(path):
            char = path[index]
            if char == "{":
                in_namespace = True
            elif char == "}":
                in_namespace = False
            elif char == "/" and not in_namespace:
                break
            index += 1
        token = path[start:index]
        if not token:
            raise SyntaxError("invalid path")
        steps.append((axis, _path_name(token, namespaces)))
        if index < len(path):
            if path[index : index + 2] == "//":
                axis = "descendant"
                index += 2
            else:
                axis = "child"
                index += 1
        if index == len(path) and path.endswith("/"):
            raise SyntaxError("invalid path")
    return steps


class Element:
    """An XML element with the small API used by the backend."""

    def __init__(self, tag: str, attrib: dict | None = None, **extra):
        self.tag = tag
        self.attrib = dict(attrib or ())
        self.attrib.update(extra)
        self.text: str | None = None
        self.tail: str | None = None
        self._children: list[Element] = []
        self._parent: Element | None = None

    def __iter__(self):
        return iter(self._children)

    def __len__(self) -> int:
        return len(self._children)

    def __getitem__(self, index):
        return self._children[index]

    def append(self, element: Element) -> None:
        element._parent = self
        self._children.append(element)

    def get(self, key: str, default=None):
        return self.attrib.get(key, default)

    def set(self, key: str, value: str) -> None:
        self.attrib[key] = value

    def iter(self, tag: str | None = None):
        if tag is None or _matches(self.tag, tag):
            yield self
        for child in self._children:
            yield from child.iter(tag)

    def _select(self, path: str, namespaces: dict[str, str] | None):
        if path == ".":
            return [self]
        current = [self]
        for axis, wanted in _path_steps(path, namespaces):
            next_items = []
            if wanted == "..":
                for element in current:
                    if element._parent is not None and element._parent not in next_items:
                        next_items.append(element._parent)
                current = next_items
                continue
            for element in current:
                candidates = (
                    (node for child in element for node in child.iter())
                    if axis == "descendant"
                    else iter(element._children)
                )
                next_items.extend(node for node in candidates if _matches(node.tag, wanted))
            current = next_items
            if not current:
                break
        return current

    def find(self, path: str, namespaces: dict[str, str] | None = None):
        matches = self._select(path, namespaces)
        return matches[0] if matches else None

    def findall(self, path: str, namespaces: dict[str, str] | None = None):
        return self._select(path, namespaces)

    def findtext(
        self,
        path: str,
        default=None,
        namespaces: dict[str, str] | None = None,
    ):
        element = self.find(path, namespaces)
        if element is None:
            return default
        return element.text if element.text is not None else ""


def SubElement(parent: Element, tag: str, attrib: dict | None = None, **extra) -> Element:
    element = Element(tag, attrib, **extra)
    parent.append(element)
    return element


def fromstring(source: str | bytes) -> Element:
    if not isinstance(source, (str, bytes)):
        raise TypeError("XML input must be str or bytes")

    parser = expat.ParserCreate(namespace_separator="}")
    root = None
    stack: list[Element] = []

    def start(name, attrs):
        nonlocal root
        element = Element(
            _expanded_name(name),
            {_expanded_name(key): value for key, value in attrs.items()},
        )
        if stack:
            stack[-1].append(element)
        else:
            root = element
        stack.append(element)

    def end(_name):
        stack.pop()

    def character_data(data):
        if not stack or not data:
            return
        element = stack[-1]
        if element._children:
            previous = element._children[-1]
            previous.tail = (previous.tail or "") + data
        else:
            element.text = (element.text or "") + data

    def reject_doctype(*_args):
        raise ParseError(
            "DOCTYPE declarations are not supported",
            position=(parser.CurrentLineNumber, parser.CurrentColumnNumber),
        )

    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.CharacterDataHandler = character_data
    parser.StartDoctypeDeclHandler = reject_doctype
    try:
        parser.Parse(source, True)
    except ParseError:
        raise
    except expat.ExpatError as exc:
        raise ParseError(
            str(exc),
            code=exc.code,
            position=(exc.lineno, exc.offset),
        ) from exc
    if root is None:
        raise ParseError("no element found", position=(1, 0))
    return root


def _escape_text(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _escape_attribute(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("attribute values must be str")
    return (
        _escape_text(value)
        .replace('"', "&quot;")
        .replace("\t", "&#09;")
        .replace("\n", "&#10;")
        .replace("\r", "&#13;")
    )


def tostring(
    element: Element,
    encoding: str = "us-ascii",
    xml_declaration: bool | None = None,
    *,
    short_empty_elements: bool = True,
):
    """Serialize an element, supporting UTF-8 output used by the backend."""
    normalized_encoding = encoding.lower().replace("_", "-")
    if normalized_encoding not in ("utf-8", "utf8", "unicode"):
        raise ValueError("xmltree only supports UTF-8 or Unicode serialization")
    if normalized_encoding == "unicode" and xml_declaration:
        raise ValueError("cannot serialize a Unicode string with an XML declaration")

    namespaces: dict[str, str] = {}

    def register(name: str) -> None:
        if name.startswith("{") and "}" in name:
            uri = name[1 : name.index("}")]
            if uri not in namespaces and uri != "http://www.w3.org/XML/1998/namespace":
                namespaces[uri] = "ns" + str(len(namespaces))

    for node in element.iter():
        register(node.tag)
        for name in node.attrib:
            register(name)

    def qname(name: str) -> str:
        if name.startswith("{") and "}" in name:
            uri, local = name[1:].split("}", 1)
            if uri == "http://www.w3.org/XML/1998/namespace":
                return "xml:" + local
            return namespaces[uri] + ":" + local
        return name

    def render(node: Element, root_node: bool = False) -> str:
        if not isinstance(node.tag, str):
            raise TypeError("element tag must be str")
        if node.text is not None and not isinstance(node.text, str):
            raise TypeError("element text must be str or None")
        if node.tail is not None and not isinstance(node.tail, str):
            raise TypeError("element tail must be str or None")
        tag = qname(node.tag)
        attrs = []
        if root_node:
            attrs.extend(
                f'xmlns:{prefix}="{_escape_attribute(uri)}"'
                for uri, prefix in namespaces.items()
            )
        attrs.extend(
            f'{qname(name)}="{_escape_attribute(value)}"'
            for name, value in node.attrib.items()
        )
        opening = "<" + tag
        if attrs:
            opening += " " + " ".join(attrs)
        if not node._children and not node.text:
            result = opening + (" />" if short_empty_elements else "></" + tag + ">")
        else:
            result = opening + ">"
            if node.text:
                result += _escape_text(node.text)
            for child in node:
                result += render(child)
                if child.tail:
                    result += _escape_text(child.tail)
            result += "</" + tag + ">"
        return result

    body = render(element, root_node=True)
    if xml_declaration:
        body = "<?xml version='1.0' encoding='utf-8'?>\n" + body
    if normalized_encoding == "unicode":
        return body
    return body.encode("utf-8")
