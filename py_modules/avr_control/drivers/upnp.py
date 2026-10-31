"""UPnP RenderingControl driver for renderers with volume and mute controls."""

from __future__ import annotations

import asyncio
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from functools import partial
from typing import ClassVar

from .. import ssdp
from .base import (
    MUTE,
    VOLUME,
    Driver,
    DriverError,
    Found,
    Refused,
    Status,
    VolumeScale,
)

SERVICE_TYPE = "urn:schemas-upnp-org:service:RenderingControl:1"
SOAP_NS = "http://schemas.xmlsoap.org/soap/envelope/"
DEFAULT_SONOS_PORT = 1400
REQUEST_TIMEOUT = 2.0
DEFAULT_MIN_VOLUME = 0
DEFAULT_MAX_VOLUME = 100
DEFAULT_VOLUME_STEP = 1
DEFAULT_CAP = 40


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _direct_text(element: ET.Element, name: str) -> str:
    for child in element:
        if _local_name(child.tag) == name:
            return (child.text or "").strip()
    return ""


def _services_in_description(root: ET.Element) -> list[tuple[ET.Element, ET.Element]]:
    services: list[tuple[ET.Element, ET.Element]] = []
    for device in root.iter():
        if _local_name(device.tag) != "device":
            continue
        for child in device:
            if _local_name(child.tag) != "serviceList":
                continue
            for service in child:
                if (
                    _local_name(service.tag) == "service"
                    and _direct_text(service, "serviceType") == SERVICE_TYPE
                    and _direct_text(service, "controlURL")
                ):
                    services.append((service, device))
    return services


def _parse_volume_range(payload: bytes) -> tuple[int, int, int] | None:
    try:
        root = ET.fromstring(payload)
    except ET.ParseError:
        return None
    for variable in root.iter():
        if _local_name(variable.tag) != "stateVariable":
            continue
        if _direct_text(variable, "name") != "Volume":
            continue
        value_range = next(
            (
                child
                for child in variable
                if _local_name(child.tag) == "allowedValueRange"
            ),
            None,
        )
        if value_range is None:
            return None
        values = {
            _local_name(child.tag): (child.text or "").strip()
            for child in value_range
        }
        try:
            minimum = int(values["minimum"])
            maximum = int(values["maximum"])
            step = int(values.get("step", "1"))
        except (KeyError, ValueError):
            return None
        if maximum < minimum or step < 1:
            return None
        return minimum, maximum, step
    return None


def _soap_fault(payload: bytes, action: str) -> Refused | None:
    try:
        root = ET.fromstring(payload)
    except ET.ParseError:
        return None
    fault = next(
        (node for node in root.iter() if _local_name(node.tag) == "Fault"), None
    )
    if fault is None:
        return None
    code = ""
    description = ""
    for node in fault.iter():
        if _local_name(node.tag) == "errorCode":
            code = (node.text or "").strip()
        elif _local_name(node.tag) == "errorDescription":
            description = (node.text or "").strip()
    message = f"UPnP receiver refused {action}"
    if code:
        message += f" (error {code}"
        if description:
            message += f": {description}"
        message += ")"
    elif description:
        message += f": {description}"
    return Refused(message)


def _response_value(payload: bytes, name: str) -> str:
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise DriverError("UPnP receiver returned invalid SOAP XML") from exc
    for node in root.iter():
        if _local_name(node.tag) == name:
            return (node.text or "").strip()
    raise DriverError(f"UPnP receiver omitted {name}")


def _http_get(url: str, timeout: float = REQUEST_TIMEOUT) -> bytes:
    request = urllib.request.Request(
        url, headers={"User-Agent": "AVR Control", "Accept": "text/xml"}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        exc.close()
        raise


def _http_post(url: str, service_type: str, action: str, body: bytes) -> bytes:
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": 'text/xml; charset="utf-8"',
            "SOAPACTION": f'"{service_type}#{action}"',
            "User-Agent": "AVR Control",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        payload = exc.read()
        fault = _soap_fault(payload, action)
        if fault:
            exc.close()
            raise fault from exc
        exc.close()
        raise DriverError(f"UPnP {action} request failed: HTTP {exc.code}") from exc


def _soap_request(service_type: str, action: str, arguments: dict[str, str]) -> bytes:
    envelope = ET.Element(
        f"{{{SOAP_NS}}}Envelope",
        {f"{{{SOAP_NS}}}encodingStyle": "http://schemas.xmlsoap.org/soap/encoding/"},
    )
    body = ET.SubElement(envelope, f"{{{SOAP_NS}}}Body")
    action_node = ET.SubElement(body, f"{{{service_type}}}{action}")
    for name, value in arguments.items():
        ET.SubElement(action_node, name).text = value
    return ET.tostring(envelope, encoding="utf-8", xml_declaration=True)


def _format_host(host: str) -> str:
    if ":" in host and not host.startswith("["):
        return f"[{host}]"
    return host


def _url_host(url: str) -> str:
    try:
        return (urllib.parse.urlsplit(url).hostname or "").strip().lower()
    except ValueError:
        return ""


def _same_host(left: str, right: str) -> bool:
    return left.strip().strip("[]").lower() == right.strip().strip("[]").lower()


def _proxy_service_url(url: str, proxy_base: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    proxy = urllib.parse.urlsplit(proxy_base)
    return urllib.parse.urlunsplit(
        (proxy.scheme, proxy.netloc, parsed.path, parsed.query, parsed.fragment)
    )


class UpnpDriver(Driver):
    key: ClassVar[str] = "upnp"
    label: ClassVar[str] = "UPnP / DLNA renderer (Sonos and others; volume and mute only)"
    identity_keys: ClassVar[tuple[str, ...]] = ("host", "udn")

    def __init__(self, config: dict):
        super().__init__(config)
        self._service_loaded = False
        self._service_lock = asyncio.Lock()
        self._control_url = ""
        self._service_type = SERVICE_TYPE
        self._scpd_url = ""
        self._service_host = ""
        self._device_info: dict[str, str] = {}
        self._scale = VolumeScale(
            min=DEFAULT_MIN_VOLUME,
            max=DEFAULT_MAX_VOLUME,
            unit="%",
            default_cap=DEFAULT_CAP,
        )

    @property
    def capabilities(self) -> frozenset[str]:
        return frozenset({VOLUME, MUTE})

    @property
    def scale(self) -> VolumeScale:
        return self._scale

    @property
    def host(self) -> str:
        return str(self.config.get("host", "")).strip()

    @property
    def location(self) -> str:
        location = str(self.config.get("location", "")).strip()
        location_host = _url_host(location)
        if not location_host or not self.host or not _same_host(
            location_host, self.host
        ):
            return ""
        return location

    async def _get(self, url: str) -> bytes:
        try:
            return await asyncio.get_running_loop().run_in_executor(
                None, partial(_http_get, url)
            )
        except DriverError:
            raise
        except Exception as exc:
            raise DriverError(f"UPnP receiver request failed: {exc}") from exc

    async def _soap(self, action: str, arguments: dict[str, str]) -> bytes:
        if not self._control_url:
            raise DriverError("UPnP RenderingControl service is unavailable")
        body = _soap_request(self._service_type, action, arguments)
        try:
            payload = await asyncio.get_running_loop().run_in_executor(
                None,
                partial(
                    _http_post,
                    self._control_url,
                    self._service_type,
                    action,
                    body,
                ),
            )
            fault = _soap_fault(payload, action)
            if fault:
                raise fault
            return payload
        except DriverError:
            raise
        except Exception as exc:
            raise DriverError(f"UPnP {action} request failed: {exc}") from exc

    async def _load_description(self, location: str) -> None:
        payload = await self._get(location)
        try:
            root = ET.fromstring(payload)
        except ET.ParseError as exc:
            raise DriverError("UPnP device returned invalid description XML") from exc
        services = _services_in_description(root)
        if not services:
            raise DriverError("UPnP device has no RenderingControl:1 service")
        selected_udn = str(self.config.get("udn", "")).strip()
        if selected_udn:
            selected = next(
                (
                    (service, device)
                    for service, device in services
                    if _direct_text(device, "UDN") == selected_udn
                ),
                None,
            )
            if selected is None:
                raise DriverError(
                    f"UPnP device has no RenderingControl service for {selected_udn}"
                )
        else:
            selected = services[0]
        await self._configure_service(root, location, *selected)

    async def _configure_service(
        self,
        root: ET.Element,
        location: str,
        service: ET.Element,
        device: ET.Element,
    ) -> None:
        control_url = _direct_text(service, "controlURL")
        scpd_url = _direct_text(service, "SCPDURL")
        base = (
            str(self.config.get("service_url_base", "")).strip()
            or _direct_text(root, "URLBase")
            or location
        )
        try:
            self._control_url = urllib.parse.urljoin(base, control_url)
            self._scpd_url = urllib.parse.urljoin(base, scpd_url) if scpd_url else ""
            proxy_base = str(self.config.get("service_url_base", "")).strip()
            if proxy_base:
                self._control_url = _proxy_service_url(self._control_url, proxy_base)
                if self._scpd_url:
                    self._scpd_url = _proxy_service_url(self._scpd_url, proxy_base)
        except ValueError as exc:
            raise DriverError("UPnP device returned an invalid service URL") from exc
        self._service_host = _url_host(location)

        # A nested renderer owns its own identity and model; fall back to the
        # root device fields only when that child omitted them.
        root_device = next(
            (node for node in root.iter() if _local_name(node.tag) == "device"),
            device,
        )
        self._device_info = {}
        for field in ("friendlyName", "manufacturer", "modelName", "UDN"):
            self._device_info[field] = (
                _direct_text(device, field) or _direct_text(root_device, field)
            )
        if self._scpd_url:
            try:
                volume_range = _parse_volume_range(await self._get(self._scpd_url))
            except DriverError:
                volume_range = None
            if volume_range:
                minimum, maximum, step = volume_range
                cap = max(minimum, min(maximum, DEFAULT_CAP))
                self._scale = VolumeScale(
                    min=minimum,
                    max=maximum,
                    unit="%",
                    zero=0,
                    steps_per_unit=1,
                    default_cap=cap,
                )
                self._volume_step = step
        self._service_loaded = True

    async def _ensure_service(self) -> None:
        if self._service_loaded and _same_host(self._service_host, self.host):
            return
        if self._service_loaded:
            self._service_loaded = False
            self._control_url = ""
            self._scpd_url = ""
            self._service_host = ""
            self._device_info = {}
            self._scale = VolumeScale(
                min=DEFAULT_MIN_VOLUME,
                max=DEFAULT_MAX_VOLUME,
                unit="%",
                default_cap=DEFAULT_CAP,
            )
            if hasattr(self, "_volume_step"):
                del self._volume_step
        async with self._service_lock:
            if self._service_loaded and _same_host(self._service_host, self.host):
                return
            if self.location:
                await self._load_description(self.location)
                return
            if not self.host:
                raise DriverError("UPnP receiver host is missing")

            try:
                sonos_port = int(self.config.get("port", DEFAULT_SONOS_PORT))
            except (TypeError, ValueError):
                sonos_port = DEFAULT_SONOS_PORT
            if not 1 <= sonos_port <= 65535:
                sonos_port = DEFAULT_SONOS_PORT
            sonos_location = (
                f"http://{_format_host(self.host)}:{sonos_port}"
                "/xml/device_description.xml"
            )
            failure: Exception | None = None
            try:
                await self._load_description(sonos_location)
                return
            except DriverError as exc:
                failure = exc
            try:
                results = await ssdp.search_host(
                    self.host,
                    SERVICE_TYPE,
                    REQUEST_TIMEOUT,
                    port=int(self.config.get("ssdp_port", 1900)),
                )
            except Exception:
                results = []
            for item in results:
                location = str(item.get("location", ""))
                if not location or not _same_host(_url_host(location), self.host):
                    continue
                try:
                    await self._load_description(location)
                    return
                except DriverError as exc:
                    failure = exc
            raise DriverError(
                f"Unable to find UPnP RenderingControl service: {failure or 'no device found'}"
            )

    def _rendering_arguments(
        self, extra: dict[str, str] | None = None
    ) -> dict[str, str]:
        return {"InstanceID": "0", "Channel": "Master", **(extra or {})}

    async def _get_volume(self) -> int:
        payload = await self._soap("GetVolume", self._rendering_arguments())
        value = _response_value(payload, "CurrentVolume")
        try:
            return int(value)
        except ValueError as exc:
            raise DriverError("UPnP receiver returned an invalid volume") from exc

    async def _get_mute(self) -> bool:
        payload = await self._soap("GetMute", self._rendering_arguments())
        value = _response_value(payload, "CurrentMute").strip().lower()
        if value in ("1", "true"):
            return True
        if value in ("0", "false"):
            return False
        raise DriverError("UPnP receiver returned an invalid mute state")

    async def status(self) -> Status:
        await self._ensure_service()
        volume = await self._get_volume()
        muted = await self._get_mute()
        return Status(volume=volume, muted=muted)

    async def set_volume(self, raw: int) -> int:
        await self._ensure_service()
        try:
            raw = int(raw)
        except (TypeError, ValueError, OverflowError) as exc:
            raise DriverError("UPnP volume must be an integer") from exc
        step = getattr(self, "_volume_step", DEFAULT_VOLUME_STEP)
        maximum = self.scale.min + (self.scale.max - self.scale.min) // step * step
        value = max(self.scale.min, min(maximum, raw))
        value = self.scale.min + (value - self.scale.min) // step * step
        value = max(self.scale.min, min(maximum, value))
        await self._soap(
            "SetVolume",
            self._rendering_arguments({"DesiredVolume": str(value)}),
        )
        return await self._get_volume()

    async def set_mute(self, muted: bool) -> bool:
        await self._ensure_service()
        await self._soap(
            "SetMute",
            self._rendering_arguments({"DesiredMute": "1" if muted else "0"}),
        )
        return await self._get_mute()

    @classmethod
    def _found(cls, host: str, location: str, info: dict[str, str]) -> Found | None:
        device_id = info.get("UDN", "").strip()
        if not device_id:
            return None
        manufacturer = info.get("manufacturer", "").strip()
        model = info.get("modelName", "").strip() or info.get(
            "friendlyName", ""
        ).strip()
        if manufacturer and model and manufacturer.lower() not in model.lower():
            model = f"{manufacturer} {model}"
        elif not model:
            model = manufacturer or "UPnP renderer"
        return Found(
            driver=cls.key,
            model=model,
            id=device_id,
            host=host,
            config={"host": host, "location": location, "udn": device_id},
        )

    @classmethod
    async def _probe_location(
        cls, host: str, location: str, udn: str = ""
    ) -> Found | None:
        driver = cls({"host": host, "location": location, "udn": udn})
        try:
            await driver._ensure_service()
            await driver._get_volume()
        except Exception:
            return None
        return cls._found(host, location, driver._device_info)

    @classmethod
    async def _discover_location(cls, host: str, location: str) -> list[Found]:
        if not _same_host(_url_host(location), host):
            return []
        description_driver = cls({"host": host, "location": location})
        try:
            payload = await description_driver._get(location)
            root = ET.fromstring(payload)
        except Exception:
            return []
        found: list[Found] = []
        for service, device in _services_in_description(root):
            driver = cls({"host": host, "location": location})
            try:
                await driver._configure_service(root, location, service, device)
                await driver._get_volume()
            except Exception:
                continue
            item = cls._found(host, location, driver._device_info)
            if item is not None:
                found.append(item)
        return found

    @classmethod
    async def discover(cls, timeout: float) -> list[Found]:
        try:
            responses = await ssdp.search(SERVICE_TYPE, max(0.0, timeout))
        except Exception:
            return []
        candidates: dict[tuple[str, str], None] = {}
        for response in responses:
            location = str(response.get("location", "")).strip()
            host = str(response.get("ip", "")).strip()
            if location and host:
                candidates[(host, location)] = None
        discovered = await asyncio.gather(
            *(cls._discover_location(host, location) for host, location in candidates)
        )
        by_id: dict[str, Found] = {}
        for group in discovered:
            for item in group:
                by_id[item.id] = item
        return list(by_id.values())

    @classmethod
    async def probe(cls, config: dict) -> Found | None:
        host = str(config.get("host", "")).strip()
        if not host:
            return None
        location = str(config.get("location", "")).strip()
        udn = str(config.get("udn", "")).strip()
        if location and _same_host(_url_host(location), host):
            return await cls._probe_location(host, location, udn)

        try:
            sonos_port = int(config.get("port", DEFAULT_SONOS_PORT))
        except (TypeError, ValueError):
            sonos_port = DEFAULT_SONOS_PORT
        if not 1 <= sonos_port <= 65535:
            sonos_port = DEFAULT_SONOS_PORT
        sonos_location = (
            f"http://{_format_host(host)}:{sonos_port}/xml/device_description.xml"
        )
        result = await cls._probe_location(host, sonos_location, udn)
        if result is not None:
            return result
        try:
            responses = await ssdp.search_host(
                host,
                SERVICE_TYPE,
                REQUEST_TIMEOUT,
                port=int(config.get("ssdp_port", 1900)),
            )
        except Exception:
            return None
        for response in responses:
            candidate = str(response.get("location", "")).strip()
            if candidate and _same_host(_url_host(candidate), host):
                result = await cls._probe_location(host, candidate, udn)
                if result is not None:
                    return result
        return None
