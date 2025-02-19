"""Test for Nest Media Source.

These tests simulate recent camera events received by the subscriber exposed
as media in the media source.
"""

import datetime
from http import HTTPStatus

import aiohttp
from google_nest_sdm.device import Device
from google_nest_sdm.event import EventMessage
import pytest

from homeassistant.components import media_source
from homeassistant.components.media_player.errors import BrowseError
from homeassistant.components.media_source import const
from homeassistant.components.media_source.error import Unresolvable
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.template import DATE_STR_FORMAT
import homeassistant.util.dt as dt_util

from .common import async_setup_sdm_platform

DOMAIN = "nest"
DEVICE_ID = "example/api/device/id"
DEVICE_NAME = "Front"
PLATFORM = "camera"
NEST_EVENT = "nest_event"
EVENT_SESSION_ID = "CjY5Y3VKaTZwR3o4Y19YbTVfMF..."
CAMERA_DEVICE_TYPE = "sdm.devices.types.CAMERA"
CAMERA_TRAITS = {
    "sdm.devices.traits.Info": {
        "customName": DEVICE_NAME,
    },
    "sdm.devices.traits.CameraImage": {},
    "sdm.devices.traits.CameraEventImage": {},
    "sdm.devices.traits.CameraPerson": {},
    "sdm.devices.traits.CameraMotion": {},
}
BATTERY_CAMERA_TRAITS = {
    "sdm.devices.traits.Info": {
        "customName": DEVICE_NAME,
    },
    "sdm.devices.traits.CameraClipPreview": {},
    "sdm.devices.traits.CameraLiveStream": {},
    "sdm.devices.traits.CameraPerson": {},
    "sdm.devices.traits.CameraMotion": {},
}
PERSON_EVENT = "sdm.devices.events.CameraPerson.Person"
MOTION_EVENT = "sdm.devices.events.CameraMotion.Motion"

TEST_IMAGE_URL = "https://domain/sdm_event_snapshot/dGTZwR3o4Y1..."
GENERATE_IMAGE_URL_RESPONSE = {
    "results": {
        "url": TEST_IMAGE_URL,
        "token": "g.0.eventToken",
    },
}
IMAGE_BYTES_FROM_EVENT = b"test url image bytes"
IMAGE_AUTHORIZATION_HEADERS = {"Authorization": "Basic g.0.eventToken"}


async def async_setup_devices(hass, auth, device_type, traits={}, events=[]):
    """Set up the platform and prerequisites."""
    devices = {
        DEVICE_ID: Device.MakeDevice(
            {
                "name": DEVICE_ID,
                "type": device_type,
                "traits": traits,
            },
            auth=auth,
        ),
    }
    subscriber = await async_setup_sdm_platform(hass, PLATFORM, devices=devices)
    if events:
        for event in events:
            await subscriber.async_receive_event(event)
        await hass.async_block_till_done()
    return subscriber


def create_event(event_id, event_type, timestamp=None):
    """Create an EventMessage for a single event type."""
    if not timestamp:
        timestamp = dt_util.now()
    event_data = {
        event_type: {
            "eventSessionId": EVENT_SESSION_ID,
            "eventId": event_id,
        },
    }
    return create_event_message(event_id, event_data, timestamp)


def create_event_message(event_id, event_data, timestamp):
    """Create an EventMessage for a single event type."""
    return EventMessage(
        {
            "eventId": f"{event_id}-{timestamp}",
            "timestamp": timestamp.isoformat(timespec="seconds"),
            "resourceUpdate": {
                "name": DEVICE_ID,
                "events": event_data,
            },
        },
        auth=None,
    )


async def test_no_eligible_devices(hass, auth):
    """Test a media source with no eligible camera devices."""
    await async_setup_devices(
        hass,
        auth,
        "sdm.devices.types.THERMOSTAT",
        {
            "sdm.devices.traits.Temperature": {},
        },
    )

    browse = await media_source.async_browse_media(hass, f"{const.URI_SCHEME}{DOMAIN}")
    assert browse.domain == DOMAIN
    assert browse.identifier == ""
    assert browse.title == "Nest"
    assert not browse.children


async def test_supported_device(hass, auth):
    """Test a media source with a supported camera."""
    await async_setup_devices(hass, auth, CAMERA_DEVICE_TYPE, CAMERA_TRAITS)

    assert len(hass.states.async_all()) == 1
    camera = hass.states.get("camera.front")
    assert camera is not None

    device_registry = dr.async_get(hass)
    device = device_registry.async_get_device({(DOMAIN, DEVICE_ID)})
    assert device
    assert device.name == DEVICE_NAME

    browse = await media_source.async_browse_media(hass, f"{const.URI_SCHEME}{DOMAIN}")
    assert browse.domain == DOMAIN
    assert browse.title == "Nest"
    assert browse.identifier == ""
    assert browse.can_expand
    assert len(browse.children) == 1
    assert browse.children[0].domain == DOMAIN
    assert browse.children[0].identifier == device.id
    assert browse.children[0].title == "Front: Recent Events"

    browse = await media_source.async_browse_media(
        hass, f"{const.URI_SCHEME}{DOMAIN}/{device.id}"
    )
    assert browse.domain == DOMAIN
    assert browse.identifier == device.id
    assert browse.title == "Front: Recent Events"
    assert len(browse.children) == 0


async def test_camera_event(hass, auth, hass_client):
    """Test a media source and image created for an event."""
    event_id = "FWWVQVUdGNUlTU2V4MGV2aTNXV..."
    event_timestamp = dt_util.now()
    await async_setup_devices(
        hass,
        auth,
        CAMERA_DEVICE_TYPE,
        CAMERA_TRAITS,
        events=[
            create_event(
                event_id,
                PERSON_EVENT,
                timestamp=event_timestamp,
            ),
        ],
    )

    assert len(hass.states.async_all()) == 1
    camera = hass.states.get("camera.front")
    assert camera is not None

    device_registry = dr.async_get(hass)
    device = device_registry.async_get_device({(DOMAIN, DEVICE_ID)})
    assert device
    assert device.name == DEVICE_NAME

    # Media root directory
    browse = await media_source.async_browse_media(hass, f"{const.URI_SCHEME}{DOMAIN}")
    assert browse.title == "Nest"
    assert browse.identifier == ""
    assert browse.can_expand
    # A device is represented as a child directory
    assert len(browse.children) == 1
    assert browse.children[0].domain == DOMAIN
    assert browse.children[0].identifier == device.id
    assert browse.children[0].title == "Front: Recent Events"
    assert browse.children[0].can_expand
    # Expanding the root does not expand the device
    assert len(browse.children[0].children) == 0

    # Browse to the device
    browse = await media_source.async_browse_media(
        hass, f"{const.URI_SCHEME}{DOMAIN}/{device.id}"
    )
    assert browse.domain == DOMAIN
    assert browse.identifier == device.id
    assert browse.title == "Front: Recent Events"
    assert browse.can_expand
    # The device expands recent events
    assert len(browse.children) == 1
    assert browse.children[0].domain == DOMAIN
    assert browse.children[0].identifier == f"{device.id}/{event_id}"
    event_timestamp_string = event_timestamp.strftime(DATE_STR_FORMAT)
    assert browse.children[0].title == f"Person @ {event_timestamp_string}"
    assert not browse.children[0].can_expand
    assert len(browse.children[0].children) == 0

    # Browse to the event
    browse = await media_source.async_browse_media(
        hass, f"{const.URI_SCHEME}{DOMAIN}/{device.id}/{event_id}"
    )
    assert browse.domain == DOMAIN
    assert browse.identifier == f"{device.id}/{event_id}"
    assert "Person" in browse.title
    assert not browse.can_expand
    assert not browse.children

    # Resolving the event links to the media
    media = await media_source.async_resolve_media(
        hass, f"{const.URI_SCHEME}{DOMAIN}/{device.id}/{event_id}"
    )
    assert media.url == f"/api/nest/event_media/{device.id}/{event_id}"
    assert media.mime_type == "image/jpeg"

    auth.responses = [
        aiohttp.web.json_response(GENERATE_IMAGE_URL_RESPONSE),
        aiohttp.web.Response(body=IMAGE_BYTES_FROM_EVENT),
    ]

    client = await hass_client()
    response = await client.get(media.url)
    assert response.status == HTTPStatus.OK, "Response not matched: %s" % response
    contents = await response.read()
    assert contents == IMAGE_BYTES_FROM_EVENT


async def test_event_order(hass, auth):
    """Test multiple events are in descending timestamp order."""
    event_id1 = "FWWVQVUdGNUlTU2V4MGV2aTNXV..."
    event_timestamp1 = dt_util.now()
    event_id2 = "GXXWRWVeHNUlUU3V3MGV3bUOYW..."
    event_timestamp2 = event_timestamp1 + datetime.timedelta(seconds=5)
    await async_setup_devices(
        hass,
        auth,
        CAMERA_DEVICE_TYPE,
        CAMERA_TRAITS,
        events=[
            create_event(
                event_id1,
                PERSON_EVENT,
                timestamp=event_timestamp1,
            ),
            create_event(
                event_id2,
                MOTION_EVENT,
                timestamp=event_timestamp2,
            ),
        ],
    )

    assert len(hass.states.async_all()) == 1
    camera = hass.states.get("camera.front")
    assert camera is not None

    device_registry = dr.async_get(hass)
    device = device_registry.async_get_device({(DOMAIN, DEVICE_ID)})
    assert device
    assert device.name == DEVICE_NAME

    browse = await media_source.async_browse_media(
        hass, f"{const.URI_SCHEME}{DOMAIN}/{device.id}"
    )
    assert browse.domain == DOMAIN
    assert browse.identifier == device.id
    assert browse.title == "Front: Recent Events"
    assert browse.can_expand

    # Motion event is most recent
    assert len(browse.children) == 2
    assert browse.children[0].domain == DOMAIN
    assert browse.children[0].identifier == f"{device.id}/{event_id2}"
    event_timestamp_string = event_timestamp2.strftime(DATE_STR_FORMAT)
    assert browse.children[0].title == f"Motion @ {event_timestamp_string}"
    assert not browse.children[0].can_expand

    # Person event is next
    assert browse.children[1].domain == DOMAIN

    assert browse.children[1].identifier == f"{device.id}/{event_id1}"
    event_timestamp_string = event_timestamp1.strftime(DATE_STR_FORMAT)
    assert browse.children[1].title == f"Person @ {event_timestamp_string}"
    assert not browse.children[1].can_expand


async def test_browse_invalid_device_id(hass, auth):
    """Test a media source request for an invalid device id."""
    await async_setup_devices(hass, auth, CAMERA_DEVICE_TYPE, CAMERA_TRAITS)

    device_registry = dr.async_get(hass)
    device = device_registry.async_get_device({(DOMAIN, DEVICE_ID)})
    assert device
    assert device.name == DEVICE_NAME

    with pytest.raises(BrowseError):
        await media_source.async_browse_media(
            hass, f"{const.URI_SCHEME}{DOMAIN}/invalid-device-id"
        )

    with pytest.raises(BrowseError):
        await media_source.async_browse_media(
            hass, f"{const.URI_SCHEME}{DOMAIN}/invalid-device-id/invalid-event-id"
        )


async def test_browse_invalid_event_id(hass, auth):
    """Test a media source browsing for an invalid event id."""
    await async_setup_devices(hass, auth, CAMERA_DEVICE_TYPE, CAMERA_TRAITS)

    device_registry = dr.async_get(hass)
    device = device_registry.async_get_device({(DOMAIN, DEVICE_ID)})
    assert device
    assert device.name == DEVICE_NAME

    browse = await media_source.async_browse_media(
        hass, f"{const.URI_SCHEME}{DOMAIN}/{device.id}"
    )
    assert browse.domain == DOMAIN
    assert browse.identifier == device.id
    assert browse.title == "Front: Recent Events"

    with pytest.raises(BrowseError):
        await media_source.async_browse_media(
            hass,
            f"{const.URI_SCHEME}{DOMAIN}/{device.id}/GXXWRWVeHNUlUU3V3MGV3bUOYW...",
        )


async def test_resolve_missing_event_id(hass, auth):
    """Test a media source request missing an event id."""
    await async_setup_devices(hass, auth, CAMERA_DEVICE_TYPE, CAMERA_TRAITS)

    device_registry = dr.async_get(hass)
    device = device_registry.async_get_device({(DOMAIN, DEVICE_ID)})
    assert device
    assert device.name == DEVICE_NAME

    with pytest.raises(Unresolvable):
        await media_source.async_resolve_media(
            hass,
            f"{const.URI_SCHEME}{DOMAIN}/{device.id}",
        )


async def test_resolve_invalid_device_id(hass, auth):
    """Test resolving media for an invalid event id."""
    await async_setup_devices(hass, auth, CAMERA_DEVICE_TYPE, CAMERA_TRAITS)

    with pytest.raises(Unresolvable):
        await media_source.async_resolve_media(
            hass,
            f"{const.URI_SCHEME}{DOMAIN}/invalid-device-id/GXXWRWVeHNUlUU3V3MGV3bUOYW...",
        )


async def test_resolve_invalid_event_id(hass, auth):
    """Test resolving media for an invalid event id."""
    await async_setup_devices(hass, auth, CAMERA_DEVICE_TYPE, CAMERA_TRAITS)

    device_registry = dr.async_get(hass)
    device = device_registry.async_get_device({(DOMAIN, DEVICE_ID)})
    assert device
    assert device.name == DEVICE_NAME

    with pytest.raises(Unresolvable):
        await media_source.async_resolve_media(
            hass,
            f"{const.URI_SCHEME}{DOMAIN}/{device.id}/GXXWRWVeHNUlUU3V3MGV3bUOYW...",
        )


async def test_camera_event_clip_preview(hass, auth, hass_client):
    """Test an event for a battery camera video clip."""
    event_id = "FWWVQVUdGNUlTU2V4MGV2aTNXV..."
    event_timestamp = dt_util.now()
    event_data = {
        "sdm.devices.events.CameraClipPreview.ClipPreview": {
            "eventSessionId": EVENT_SESSION_ID,
            "previewUrl": "https://127.0.0.1/example",
        },
    }
    await async_setup_devices(
        hass,
        auth,
        CAMERA_DEVICE_TYPE,
        BATTERY_CAMERA_TRAITS,
        events=[
            create_event_message(
                event_id,
                event_data,
                timestamp=event_timestamp,
            ),
        ],
    )

    assert len(hass.states.async_all()) == 1
    camera = hass.states.get("camera.front")
    assert camera is not None

    device_registry = dr.async_get(hass)
    device = device_registry.async_get_device({(DOMAIN, DEVICE_ID)})
    assert device
    assert device.name == DEVICE_NAME

    # Browse to the device
    browse = await media_source.async_browse_media(
        hass, f"{const.URI_SCHEME}{DOMAIN}/{device.id}"
    )
    assert browse.domain == DOMAIN
    assert browse.identifier == device.id
    assert browse.title == "Front: Recent Events"
    assert browse.can_expand
    # The device expands recent events
    assert len(browse.children) == 1
    assert browse.children[0].domain == DOMAIN
    actual_event_id = browse.children[0].identifier
    event_timestamp_string = event_timestamp.strftime(DATE_STR_FORMAT)
    assert browse.children[0].title == f"Event @ {event_timestamp_string}"
    assert not browse.children[0].can_expand
    assert len(browse.children[0].children) == 0

    # Resolving the event links to the media
    media = await media_source.async_resolve_media(
        hass, f"{const.URI_SCHEME}{DOMAIN}/{actual_event_id}"
    )
    assert media.url == f"/api/nest/event_media/{actual_event_id}"
    assert media.mime_type == "video/mp4"

    auth.responses = [
        aiohttp.web.Response(body=IMAGE_BYTES_FROM_EVENT),
    ]

    client = await hass_client()
    response = await client.get(media.url)
    assert response.status == HTTPStatus.OK, "Response not matched: %s" % response
    contents = await response.read()
    assert contents == IMAGE_BYTES_FROM_EVENT


async def test_event_media_render_invalid_device_id(hass, auth, hass_client):
    """Test event media API called with an invalid device id."""
    await async_setup_devices(hass, auth, CAMERA_DEVICE_TYPE, CAMERA_TRAITS)

    client = await hass_client()
    response = await client.get("/api/nest/event_media/invalid-device-id")
    assert response.status == HTTPStatus.NOT_FOUND, (
        "Response not matched: %s" % response
    )


async def test_event_media_render_invalid_event_id(hass, auth, hass_client):
    """Test event media API called with an invalid device id."""
    await async_setup_devices(hass, auth, CAMERA_DEVICE_TYPE, CAMERA_TRAITS)

    device_registry = dr.async_get(hass)
    device = device_registry.async_get_device({(DOMAIN, DEVICE_ID)})
    assert device
    assert device.name == DEVICE_NAME

    client = await hass_client()
    response = await client.get("/api/nest/event_media/{device.id}/invalid-event-id")
    assert response.status == HTTPStatus.NOT_FOUND, (
        "Response not matched: %s" % response
    )


async def test_event_media_failure(hass, auth, hass_client):
    """Test event media fetch sees a failure from the server."""
    event_id = "FWWVQVUdGNUlTU2V4MGV2aTNXV..."
    event_timestamp = dt_util.now()
    await async_setup_devices(
        hass,
        auth,
        CAMERA_DEVICE_TYPE,
        CAMERA_TRAITS,
        events=[
            create_event(
                event_id,
                PERSON_EVENT,
                timestamp=event_timestamp,
            ),
        ],
    )

    assert len(hass.states.async_all()) == 1
    camera = hass.states.get("camera.front")
    assert camera is not None

    device_registry = dr.async_get(hass)
    device = device_registry.async_get_device({(DOMAIN, DEVICE_ID)})
    assert device
    assert device.name == DEVICE_NAME

    # Resolving the event links to the media
    media = await media_source.async_resolve_media(
        hass, f"{const.URI_SCHEME}{DOMAIN}/{device.id}/{event_id}"
    )
    assert media.url == f"/api/nest/event_media/{device.id}/{event_id}"
    assert media.mime_type == "image/jpeg"

    auth.responses = [
        aiohttp.web.Response(status=HTTPStatus.INTERNAL_SERVER_ERROR),
    ]

    client = await hass_client()
    response = await client.get(media.url)
    assert response.status == HTTPStatus.INTERNAL_SERVER_ERROR, (
        "Response not matched: %s" % response
    )


async def test_media_permission_unauthorized(hass, auth, hass_client, hass_admin_user):
    """Test case where user does not have permissions to view media."""
    event_id = "FWWVQVUdGNUlTU2V4MGV2aTNXV..."
    event_timestamp = dt_util.now()
    await async_setup_devices(
        hass,
        auth,
        CAMERA_DEVICE_TYPE,
        CAMERA_TRAITS,
        events=[
            create_event(
                event_id,
                PERSON_EVENT,
                timestamp=event_timestamp,
            ),
        ],
    )

    assert len(hass.states.async_all()) == 1
    camera = hass.states.get("camera.front")
    assert camera is not None

    device_registry = dr.async_get(hass)
    device = device_registry.async_get_device({(DOMAIN, DEVICE_ID)})
    assert device
    assert device.name == DEVICE_NAME

    media_url = f"/api/nest/event_media/{device.id}/{event_id}"

    # Empty policy with no access to the entity
    hass_admin_user.mock_policy({})

    client = await hass_client()
    response = await client.get(media_url)
    assert response.status == HTTPStatus.UNAUTHORIZED, (
        "Response not matched: %s" % response
    )
