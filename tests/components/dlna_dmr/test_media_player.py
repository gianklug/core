"""Tests for the DLNA DMR media_player module."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterable, Mapping
from datetime import timedelta
from types import MappingProxyType
from typing import Any
from unittest.mock import ANY, DEFAULT, Mock, patch

from async_upnp_client import UpnpService, UpnpStateVariable
from async_upnp_client.exceptions import (
    UpnpConnectionError,
    UpnpError,
    UpnpResponseError,
)
from async_upnp_client.profiles.dlna import PlayMode, TransportState
import pytest

from homeassistant import const as ha_const
from homeassistant.components import ssdp
from homeassistant.components.dlna_dmr import media_player
from homeassistant.components.dlna_dmr.const import (
    CONF_CALLBACK_URL_OVERRIDE,
    CONF_LISTEN_PORT,
    CONF_POLL_AVAILABILITY,
    DOMAIN as DLNA_DOMAIN,
)
from homeassistant.components.dlna_dmr.data import EventListenAddr
from homeassistant.components.media_player import ATTR_TO_PROPERTY, const as mp_const
from homeassistant.components.media_player.const import DOMAIN as MP_DOMAIN
from homeassistant.const import ATTR_ENTITY_ID, CONF_PLATFORM, CONF_URL
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import async_get as async_get_dr
from homeassistant.helpers.entity_component import async_update_entity
from homeassistant.helpers.entity_registry import (
    async_entries_for_config_entry,
    async_get as async_get_er,
)
from homeassistant.helpers.typing import ConfigType
from homeassistant.setup import async_setup_component

from .conftest import (
    LOCAL_IP,
    MOCK_DEVICE_LOCATION,
    MOCK_DEVICE_NAME,
    MOCK_DEVICE_UDN,
    MOCK_DEVICE_USN,
    NEW_DEVICE_LOCATION,
)

from tests.common import MockConfigEntry

MOCK_DEVICE_ST = "mock_st"

# Auto-use the domain_data_mock fixture for every test in this module
pytestmark = pytest.mark.usefixtures("domain_data_mock")


async def setup_mock_component(hass: HomeAssistant, mock_entry: MockConfigEntry) -> str:
    """Set up a mock DlnaDmrEntity with the given configuration."""
    mock_entry.add_to_hass(hass)
    assert await async_setup_component(hass, DLNA_DOMAIN, {}) is True
    await hass.async_block_till_done()

    entries = async_entries_for_config_entry(async_get_er(hass), mock_entry.entry_id)
    assert len(entries) == 1
    entity_id = entries[0].entity_id

    return entity_id


async def get_attrs(hass: HomeAssistant, entity_id: str) -> Mapping[str, Any]:
    """Get updated device attributes."""
    await async_update_entity(hass, entity_id)
    entity_state = hass.states.get(entity_id)
    assert entity_state is not None
    attrs = entity_state.attributes
    assert attrs is not None
    return attrs


@pytest.fixture
async def mock_entity_id(
    hass: HomeAssistant,
    domain_data_mock: Mock,
    config_entry_mock: MockConfigEntry,
    ssdp_scanner_mock: Mock,
    dmr_device_mock: Mock,
) -> AsyncIterable[str]:
    """Fixture to set up a mock DlnaDmrEntity in a connected state.

    Yields the entity ID. Cleans up the entity after the test is complete.
    """
    entity_id = await setup_mock_component(hass, config_entry_mock)

    # Check the entity has registered all needed listeners
    assert len(config_entry_mock.update_listeners) == 1
    assert domain_data_mock.async_get_event_notifier.await_count == 1
    assert domain_data_mock.async_release_event_notifier.await_count == 0
    assert ssdp_scanner_mock.async_register_callback.await_count == 2
    assert ssdp_scanner_mock.async_register_callback.return_value.call_count == 0
    assert dmr_device_mock.async_subscribe_services.await_count == 1
    assert dmr_device_mock.async_unsubscribe_services.await_count == 0
    assert dmr_device_mock.on_event is not None

    # Run the test
    yield entity_id

    # Unload config entry to clean up
    assert await hass.config_entries.async_remove(config_entry_mock.entry_id) == {
        "require_restart": False
    }

    # Check entity has cleaned up its resources
    assert not config_entry_mock.update_listeners
    assert (
        domain_data_mock.async_get_event_notifier.await_count
        == domain_data_mock.async_release_event_notifier.await_count
    )
    assert (
        ssdp_scanner_mock.async_register_callback.await_count
        == ssdp_scanner_mock.async_register_callback.return_value.call_count
    )
    assert (
        dmr_device_mock.async_subscribe_services.await_count
        == dmr_device_mock.async_unsubscribe_services.await_count
    )
    assert dmr_device_mock.on_event is None


@pytest.fixture
async def mock_disconnected_entity_id(
    hass: HomeAssistant,
    domain_data_mock: Mock,
    config_entry_mock: MockConfigEntry,
    ssdp_scanner_mock: Mock,
    dmr_device_mock: Mock,
) -> AsyncIterable[str]:
    """Fixture to set up a mock DlnaDmrEntity in a disconnected state.

    Yields the entity ID. Cleans up the entity after the test is complete.
    """
    # Cause the connection attempt to fail
    domain_data_mock.upnp_factory.async_create_device.side_effect = UpnpConnectionError

    entity_id = await setup_mock_component(hass, config_entry_mock)

    # Check the entity has registered all needed listeners
    assert len(config_entry_mock.update_listeners) == 1
    assert ssdp_scanner_mock.async_register_callback.await_count == 2
    assert ssdp_scanner_mock.async_register_callback.return_value.call_count == 0

    # The DmrDevice hasn't been instantiated yet
    assert domain_data_mock.async_get_event_notifier.await_count == 0
    assert domain_data_mock.async_release_event_notifier.await_count == 0
    assert dmr_device_mock.async_subscribe_services.await_count == 0
    assert dmr_device_mock.async_unsubscribe_services.await_count == 0
    assert dmr_device_mock.on_event is None

    # Run the test
    yield entity_id

    # Unload config entry to clean up
    assert await hass.config_entries.async_remove(config_entry_mock.entry_id) == {
        "require_restart": False
    }

    # Check entity has cleaned up its resources
    assert not config_entry_mock.update_listeners
    assert (
        domain_data_mock.async_get_event_notifier.await_count
        == domain_data_mock.async_release_event_notifier.await_count
    )
    assert (
        ssdp_scanner_mock.async_register_callback.await_count
        == ssdp_scanner_mock.async_register_callback.return_value.call_count
    )
    assert (
        dmr_device_mock.async_subscribe_services.await_count
        == dmr_device_mock.async_unsubscribe_services.await_count
    )
    assert dmr_device_mock.on_event is None


async def test_setup_platform_import_flow_started(
    hass: HomeAssistant, domain_data_mock: Mock
) -> None:
    """Test import flow of YAML config is started if there's config data."""
    # Cause connection attempts to fail
    domain_data_mock.upnp_factory.async_create_device.side_effect = UpnpConnectionError

    # Run the setup
    mock_config: ConfigType = {
        MP_DOMAIN: [
            {
                CONF_PLATFORM: DLNA_DOMAIN,
                CONF_URL: MOCK_DEVICE_LOCATION,
                CONF_LISTEN_PORT: 1234,
            }
        ]
    }

    await async_setup_component(hass, MP_DOMAIN, mock_config)
    await hass.async_block_till_done()

    # Check config_flow has started
    flows = hass.config_entries.flow.async_progress(include_uninitialized=True)
    assert len(flows) == 1

    # It should be paused, waiting for the user to turn on the device
    flow = flows[0]
    assert flow["handler"] == "dlna_dmr"
    assert flow["step_id"] == "import_turn_on"
    assert flow["context"].get("unique_id") == MOCK_DEVICE_LOCATION


async def test_setup_entry_no_options(
    hass: HomeAssistant,
    domain_data_mock: Mock,
    ssdp_scanner_mock: Mock,
    config_entry_mock: MockConfigEntry,
    dmr_device_mock: Mock,
) -> None:
    """Test async_setup_entry creates a DlnaDmrEntity when no options are set.

    Check that the device is constructed properly as part of the test.
    """
    config_entry_mock.options = MappingProxyType({})
    mock_entity_id = await setup_mock_component(hass, config_entry_mock)
    mock_state = hass.states.get(mock_entity_id)
    assert mock_state is not None

    # Check device was created from the supplied URL
    domain_data_mock.upnp_factory.async_create_device.assert_awaited_once_with(
        MOCK_DEVICE_LOCATION
    )
    # Check event notifiers are acquired
    domain_data_mock.async_get_event_notifier.assert_awaited_once_with(
        EventListenAddr(LOCAL_IP, 0, None), hass
    )
    # Check UPnP services are subscribed
    dmr_device_mock.async_subscribe_services.assert_awaited_once_with(
        auto_resubscribe=True
    )
    assert dmr_device_mock.on_event is not None
    # Check SSDP notifications are registered
    ssdp_scanner_mock.async_register_callback.assert_any_call(
        ANY, {"USN": MOCK_DEVICE_USN}
    )
    ssdp_scanner_mock.async_register_callback.assert_any_call(
        ANY, {"_udn": MOCK_DEVICE_UDN, "NTS": "ssdp:byebye"}
    )
    # Quick check of the state to verify the entity has a connected DmrDevice
    assert mock_state.state == media_player.STATE_IDLE
    # Check the name matches that supplied
    assert mock_state.name == MOCK_DEVICE_NAME

    # Check that an update retrieves state from the device, but does not ping,
    # because poll_availability is False
    await async_update_entity(hass, mock_entity_id)
    dmr_device_mock.async_update.assert_awaited_with(do_ping=False)

    # Unload config entry to clean up
    assert await hass.config_entries.async_remove(config_entry_mock.entry_id) == {
        "require_restart": False
    }

    # Confirm SSDP notifications unregistered
    assert ssdp_scanner_mock.async_register_callback.return_value.call_count == 2

    # Confirm the entity has disconnected from the device
    domain_data_mock.async_release_event_notifier.assert_awaited_once()
    dmr_device_mock.async_unsubscribe_services.assert_awaited_once()
    assert dmr_device_mock.on_event is None
    mock_state = hass.states.get(mock_entity_id)
    assert mock_state is not None
    assert mock_state.state == ha_const.STATE_UNAVAILABLE


async def test_setup_entry_with_options(
    hass: HomeAssistant,
    domain_data_mock: Mock,
    ssdp_scanner_mock: Mock,
    config_entry_mock: MockConfigEntry,
    dmr_device_mock: Mock,
) -> None:
    """Test setting options leads to a DlnaDmrEntity with custom event_handler.

    Check that the device is constructed properly as part of the test.
    """
    config_entry_mock.options = MappingProxyType(
        {
            CONF_LISTEN_PORT: 2222,
            CONF_CALLBACK_URL_OVERRIDE: "http://192.88.99.10/events",
            CONF_POLL_AVAILABILITY: True,
        }
    )
    mock_entity_id = await setup_mock_component(hass, config_entry_mock)
    mock_state = hass.states.get(mock_entity_id)
    assert mock_state is not None

    # Check device was created from the supplied URL
    domain_data_mock.upnp_factory.async_create_device.assert_awaited_once_with(
        MOCK_DEVICE_LOCATION
    )
    # Check event notifiers are acquired with the configured port and callback URL
    domain_data_mock.async_get_event_notifier.assert_awaited_once_with(
        EventListenAddr(LOCAL_IP, 2222, "http://192.88.99.10/events"), hass
    )
    # Check UPnP services are subscribed
    dmr_device_mock.async_subscribe_services.assert_awaited_once_with(
        auto_resubscribe=True
    )
    assert dmr_device_mock.on_event is not None
    # Check SSDP notifications are registered
    ssdp_scanner_mock.async_register_callback.assert_any_call(
        ANY, {"USN": MOCK_DEVICE_USN}
    )
    ssdp_scanner_mock.async_register_callback.assert_any_call(
        ANY, {"_udn": MOCK_DEVICE_UDN, "NTS": "ssdp:byebye"}
    )
    # Quick check of the state to verify the entity has a connected DmrDevice
    assert mock_state.state == media_player.STATE_IDLE
    # Check the name matches that supplied
    assert mock_state.name == MOCK_DEVICE_NAME

    # Check that an update retrieves state from the device, and also pings it,
    # because poll_availability is True
    await async_update_entity(hass, mock_entity_id)
    dmr_device_mock.async_update.assert_awaited_with(do_ping=True)

    # Unload config entry to clean up
    assert await hass.config_entries.async_remove(config_entry_mock.entry_id) == {
        "require_restart": False
    }

    # Confirm SSDP notifications unregistered
    assert ssdp_scanner_mock.async_register_callback.return_value.call_count == 2

    # Confirm the entity has disconnected from the device
    domain_data_mock.async_release_event_notifier.assert_awaited_once()
    dmr_device_mock.async_unsubscribe_services.assert_awaited_once()
    assert dmr_device_mock.on_event is None
    mock_state = hass.states.get(mock_entity_id)
    assert mock_state is not None
    assert mock_state.state == ha_const.STATE_UNAVAILABLE


async def test_event_subscribe_failure(
    hass: HomeAssistant, config_entry_mock: MockConfigEntry, dmr_device_mock: Mock
) -> None:
    """Test _device_connect aborts when async_subscribe_services fails."""
    dmr_device_mock.async_subscribe_services.side_effect = UpnpError

    mock_entity_id = await setup_mock_component(hass, config_entry_mock)
    mock_state = hass.states.get(mock_entity_id)
    assert mock_state is not None

    # Device should not be connected
    assert mock_state.state == ha_const.STATE_UNAVAILABLE

    # Device should not be unsubscribed
    dmr_device_mock.async_unsubscribe_services.assert_not_awaited()

    # Clear mocks for tear down checks
    dmr_device_mock.async_subscribe_services.reset_mock()

    # Unload config entry to clean up
    assert await hass.config_entries.async_remove(config_entry_mock.entry_id) == {
        "require_restart": False
    }


async def test_event_subscribe_rejected(
    hass: HomeAssistant,
    config_entry_mock: MockConfigEntry,
    dmr_device_mock: Mock,
) -> None:
    """Test _device_connect continues when the device rejects a subscription.

    Device state will instead be obtained via polling in async_update.
    """
    dmr_device_mock.async_subscribe_services.side_effect = UpnpResponseError(501)

    mock_entity_id = await setup_mock_component(hass, config_entry_mock)
    mock_state = hass.states.get(mock_entity_id)
    assert mock_state is not None

    # Device should be connected
    assert mock_state.state == ha_const.STATE_IDLE

    # Device should not be unsubscribed
    dmr_device_mock.async_unsubscribe_services.assert_not_awaited()

    # Unload config entry to clean up
    assert await hass.config_entries.async_remove(config_entry_mock.entry_id) == {
        "require_restart": False
    }


async def test_available_device(
    hass: HomeAssistant, dmr_device_mock: Mock, mock_entity_id: str
) -> None:
    """Test a DlnaDmrEntity with a connected DmrDevice."""
    # Check hass device information is filled in
    dev_reg = async_get_dr(hass)
    device = dev_reg.async_get_device(identifiers={(DLNA_DOMAIN, MOCK_DEVICE_UDN)})
    assert device is not None
    # Device properties are set in dmr_device_mock before the entity gets constructed
    assert device.manufacturer == "device_manufacturer"
    assert device.model == "device_model_name"
    assert device.name == "device_name"

    # Check entity state gets updated when device changes state
    for (dev_state, ent_state) in [
        (None, ha_const.STATE_ON),
        (TransportState.STOPPED, ha_const.STATE_IDLE),
        (TransportState.PLAYING, ha_const.STATE_PLAYING),
        (TransportState.TRANSITIONING, ha_const.STATE_PLAYING),
        (TransportState.PAUSED_PLAYBACK, ha_const.STATE_PAUSED),
        (TransportState.PAUSED_RECORDING, ha_const.STATE_PAUSED),
        (TransportState.RECORDING, ha_const.STATE_IDLE),
        (TransportState.NO_MEDIA_PRESENT, ha_const.STATE_IDLE),
        (TransportState.VENDOR_DEFINED, ha_const.STATE_UNKNOWN),
    ]:
        dmr_device_mock.profile_device.available = True
        dmr_device_mock.transport_state = dev_state
        await async_update_entity(hass, mock_entity_id)
        entity_state = hass.states.get(mock_entity_id)
        assert entity_state is not None
        assert entity_state.state == ent_state

    dmr_device_mock.profile_device.available = False
    dmr_device_mock.transport_state = TransportState.PLAYING
    await async_update_entity(hass, mock_entity_id)
    entity_state = hass.states.get(mock_entity_id)
    assert entity_state is not None
    assert entity_state.state == ha_const.STATE_UNAVAILABLE


async def test_feature_flags(
    hass: HomeAssistant, dmr_device_mock: Mock, mock_entity_id: str
) -> None:
    """Test feature flags of a connected DlnaDmrEntity."""
    # Check supported feature flags, one at a time.
    FEATURE_FLAGS: list[tuple[str, int]] = [
        ("has_volume_level", mp_const.SUPPORT_VOLUME_SET),
        ("has_volume_mute", mp_const.SUPPORT_VOLUME_MUTE),
        ("can_play", mp_const.SUPPORT_PLAY),
        ("can_pause", mp_const.SUPPORT_PAUSE),
        ("can_stop", mp_const.SUPPORT_STOP),
        ("can_previous", mp_const.SUPPORT_PREVIOUS_TRACK),
        ("can_next", mp_const.SUPPORT_NEXT_TRACK),
        ("has_play_media", mp_const.SUPPORT_PLAY_MEDIA),
        ("can_seek_rel_time", mp_const.SUPPORT_SEEK),
        ("has_presets", mp_const.SUPPORT_SELECT_SOUND_MODE),
    ]

    # Clear all feature properties
    dmr_device_mock.valid_play_modes = set()
    for feat_prop, _ in FEATURE_FLAGS:
        setattr(dmr_device_mock, feat_prop, False)
    attrs = await get_attrs(hass, mock_entity_id)
    assert attrs[ha_const.ATTR_SUPPORTED_FEATURES] == 0

    # Test the properties cumulatively
    expected_features = 0
    for feat_prop, flag in FEATURE_FLAGS:
        setattr(dmr_device_mock, feat_prop, True)
        expected_features |= flag
        attrs = await get_attrs(hass, mock_entity_id)
        assert attrs[ha_const.ATTR_SUPPORTED_FEATURES] == expected_features

    # shuffle and repeat features depend on the available play modes
    PLAY_MODE_FEATURE_FLAGS: list[tuple[PlayMode, int]] = [
        (PlayMode.NORMAL, 0),
        (PlayMode.SHUFFLE, mp_const.SUPPORT_SHUFFLE_SET),
        (PlayMode.REPEAT_ONE, mp_const.SUPPORT_REPEAT_SET),
        (PlayMode.REPEAT_ALL, mp_const.SUPPORT_REPEAT_SET),
        (PlayMode.RANDOM, mp_const.SUPPORT_SHUFFLE_SET),
        (PlayMode.DIRECT_1, 0),
        (PlayMode.INTRO, 0),
        (PlayMode.VENDOR_DEFINED, 0),
    ]
    for play_modes, flag in PLAY_MODE_FEATURE_FLAGS:
        dmr_device_mock.valid_play_modes = {play_modes}
        attrs = await get_attrs(hass, mock_entity_id)
        assert attrs[ha_const.ATTR_SUPPORTED_FEATURES] == expected_features | flag


async def test_attributes(
    hass: HomeAssistant, dmr_device_mock: Mock, mock_entity_id: str
) -> None:
    """Test attributes of a connected DlnaDmrEntity."""
    # Check attributes come directly from the device
    attrs = await get_attrs(hass, mock_entity_id)
    assert attrs[mp_const.ATTR_MEDIA_VOLUME_LEVEL] is dmr_device_mock.volume_level
    assert attrs[mp_const.ATTR_MEDIA_VOLUME_MUTED] is dmr_device_mock.is_volume_muted
    assert attrs[mp_const.ATTR_MEDIA_DURATION] is dmr_device_mock.media_duration
    assert attrs[mp_const.ATTR_MEDIA_POSITION] is dmr_device_mock.media_position
    assert (
        attrs[mp_const.ATTR_MEDIA_POSITION_UPDATED_AT]
        is dmr_device_mock.media_position_updated_at
    )
    assert attrs[mp_const.ATTR_MEDIA_CONTENT_ID] is dmr_device_mock.current_track_uri
    assert attrs[mp_const.ATTR_MEDIA_ARTIST] is dmr_device_mock.media_artist
    assert attrs[mp_const.ATTR_MEDIA_ALBUM_NAME] is dmr_device_mock.media_album_name
    assert attrs[mp_const.ATTR_MEDIA_ALBUM_ARTIST] is dmr_device_mock.media_album_artist
    assert attrs[mp_const.ATTR_MEDIA_TRACK] is dmr_device_mock.media_track_number
    assert attrs[mp_const.ATTR_MEDIA_SERIES_TITLE] is dmr_device_mock.media_series_title
    assert attrs[mp_const.ATTR_MEDIA_SEASON] is dmr_device_mock.media_season_number
    assert attrs[mp_const.ATTR_MEDIA_EPISODE] is dmr_device_mock.media_episode_number
    assert attrs[mp_const.ATTR_MEDIA_CHANNEL] is dmr_device_mock.media_channel_name
    assert attrs[mp_const.ATTR_SOUND_MODE_LIST] is dmr_device_mock.preset_names

    # Entity picture is cached, won't correspond to remote image
    assert isinstance(attrs[ha_const.ATTR_ENTITY_PICTURE], str)

    # media_title depends on what is available
    assert attrs[mp_const.ATTR_MEDIA_TITLE] is dmr_device_mock.media_program_title
    dmr_device_mock.media_program_title = None
    attrs = await get_attrs(hass, mock_entity_id)
    assert attrs[mp_const.ATTR_MEDIA_TITLE] is dmr_device_mock.media_title

    # media_content_type is mapped from UPnP class to MediaPlayer type
    dmr_device_mock.media_class = "object.item.audioItem.musicTrack"
    attrs = await get_attrs(hass, mock_entity_id)
    assert attrs[mp_const.ATTR_MEDIA_CONTENT_TYPE] == mp_const.MEDIA_TYPE_MUSIC
    dmr_device_mock.media_class = "object.item.videoItem.movie"
    attrs = await get_attrs(hass, mock_entity_id)
    assert attrs[mp_const.ATTR_MEDIA_CONTENT_TYPE] == mp_const.MEDIA_TYPE_MOVIE
    dmr_device_mock.media_class = "object.item.videoItem.videoBroadcast"
    attrs = await get_attrs(hass, mock_entity_id)
    assert attrs[mp_const.ATTR_MEDIA_CONTENT_TYPE] == mp_const.MEDIA_TYPE_TVSHOW

    # media_season & media_episode have a special case
    dmr_device_mock.media_season_number = "0"
    dmr_device_mock.media_episode_number = "123"
    attrs = await get_attrs(hass, mock_entity_id)
    assert attrs[mp_const.ATTR_MEDIA_SEASON] == "1"
    assert attrs[mp_const.ATTR_MEDIA_EPISODE] == "23"
    dmr_device_mock.media_season_number = "0"
    dmr_device_mock.media_episode_number = "S1E23"  # Unexpected and not parsed
    attrs = await get_attrs(hass, mock_entity_id)
    assert attrs[mp_const.ATTR_MEDIA_SEASON] == "0"
    assert attrs[mp_const.ATTR_MEDIA_EPISODE] == "S1E23"

    # shuffle and repeat is based on device's play mode
    for play_mode, shuffle, repeat in [
        (PlayMode.NORMAL, False, mp_const.REPEAT_MODE_OFF),
        (PlayMode.SHUFFLE, True, mp_const.REPEAT_MODE_OFF),
        (PlayMode.REPEAT_ONE, False, mp_const.REPEAT_MODE_ONE),
        (PlayMode.REPEAT_ALL, False, mp_const.REPEAT_MODE_ALL),
        (PlayMode.RANDOM, True, mp_const.REPEAT_MODE_ALL),
        (PlayMode.DIRECT_1, False, mp_const.REPEAT_MODE_OFF),
        (PlayMode.INTRO, False, mp_const.REPEAT_MODE_OFF),
    ]:
        dmr_device_mock.play_mode = play_mode
        attrs = await get_attrs(hass, mock_entity_id)
        assert attrs[mp_const.ATTR_MEDIA_SHUFFLE] is shuffle
        assert attrs[mp_const.ATTR_MEDIA_REPEAT] == repeat
    for bad_play_mode in [None, PlayMode.VENDOR_DEFINED]:
        dmr_device_mock.play_mode = bad_play_mode
        attrs = await get_attrs(hass, mock_entity_id)
        assert mp_const.ATTR_MEDIA_SHUFFLE not in attrs
        assert mp_const.ATTR_MEDIA_REPEAT not in attrs


async def test_services(
    hass: HomeAssistant, dmr_device_mock: Mock, mock_entity_id: str
) -> None:
    """Test service calls of a connected DlnaDmrEntity."""
    # Check interface methods interact directly with the device
    await hass.services.async_call(
        MP_DOMAIN,
        ha_const.SERVICE_VOLUME_SET,
        {ATTR_ENTITY_ID: mock_entity_id, mp_const.ATTR_MEDIA_VOLUME_LEVEL: 0.80},
        blocking=True,
    )
    dmr_device_mock.async_set_volume_level.assert_awaited_once_with(0.80)
    await hass.services.async_call(
        MP_DOMAIN,
        ha_const.SERVICE_VOLUME_MUTE,
        {ATTR_ENTITY_ID: mock_entity_id, mp_const.ATTR_MEDIA_VOLUME_MUTED: True},
        blocking=True,
    )
    dmr_device_mock.async_mute_volume.assert_awaited_once_with(True)
    await hass.services.async_call(
        MP_DOMAIN,
        ha_const.SERVICE_MEDIA_PAUSE,
        {ATTR_ENTITY_ID: mock_entity_id},
        blocking=True,
    )
    dmr_device_mock.async_pause.assert_awaited_once_with()
    await hass.services.async_call(
        MP_DOMAIN,
        ha_const.SERVICE_MEDIA_PLAY,
        {ATTR_ENTITY_ID: mock_entity_id},
        blocking=True,
    )
    dmr_device_mock.async_pause.assert_awaited_once_with()
    await hass.services.async_call(
        MP_DOMAIN,
        ha_const.SERVICE_MEDIA_STOP,
        {ATTR_ENTITY_ID: mock_entity_id},
        blocking=True,
    )
    dmr_device_mock.async_stop.assert_awaited_once_with()
    await hass.services.async_call(
        MP_DOMAIN,
        ha_const.SERVICE_MEDIA_NEXT_TRACK,
        {ATTR_ENTITY_ID: mock_entity_id},
        blocking=True,
    )
    dmr_device_mock.async_next.assert_awaited_once_with()
    await hass.services.async_call(
        MP_DOMAIN,
        ha_const.SERVICE_MEDIA_PREVIOUS_TRACK,
        {ATTR_ENTITY_ID: mock_entity_id},
        blocking=True,
    )
    dmr_device_mock.async_previous.assert_awaited_once_with()
    await hass.services.async_call(
        MP_DOMAIN,
        ha_const.SERVICE_MEDIA_SEEK,
        {ATTR_ENTITY_ID: mock_entity_id, mp_const.ATTR_MEDIA_SEEK_POSITION: 33},
        blocking=True,
    )
    dmr_device_mock.async_seek_rel_time.assert_awaited_once_with(timedelta(seconds=33))
    await hass.services.async_call(
        MP_DOMAIN,
        mp_const.SERVICE_SELECT_SOUND_MODE,
        {ATTR_ENTITY_ID: mock_entity_id, mp_const.ATTR_SOUND_MODE: "Default"},
        blocking=True,
    )
    dmr_device_mock.async_select_preset.assert_awaited_once_with("Default")


async def test_play_media_stopped(
    hass: HomeAssistant, dmr_device_mock: Mock, mock_entity_id: str
) -> None:
    """Test play_media, starting from stopped and the device can stop."""
    # play_media performs a few calls to the device for setup and play
    dmr_device_mock.can_stop = True
    dmr_device_mock.transport_state = TransportState.STOPPED
    await hass.services.async_call(
        MP_DOMAIN,
        mp_const.SERVICE_PLAY_MEDIA,
        {
            ATTR_ENTITY_ID: mock_entity_id,
            mp_const.ATTR_MEDIA_CONTENT_TYPE: mp_const.MEDIA_TYPE_MUSIC,
            mp_const.ATTR_MEDIA_CONTENT_ID: "http://192.88.99.20:8200/MediaItems/17621.mp3",
            mp_const.ATTR_MEDIA_ENQUEUE: False,
        },
        blocking=True,
    )

    dmr_device_mock.construct_play_media_metadata.assert_awaited_once_with(
        media_url="http://192.88.99.20:8200/MediaItems/17621.mp3",
        media_title="Home Assistant",
        override_upnp_class="object.item.audioItem.musicTrack",
        meta_data={},
    )
    dmr_device_mock.async_stop.assert_awaited_once_with()
    dmr_device_mock.async_set_transport_uri.assert_awaited_once_with(
        "http://192.88.99.20:8200/MediaItems/17621.mp3", "Home Assistant", ANY
    )
    dmr_device_mock.async_wait_for_can_play.assert_awaited_once_with()
    dmr_device_mock.async_play.assert_awaited_once_with()


async def test_play_media_playing(
    hass: HomeAssistant, dmr_device_mock: Mock, mock_entity_id: str
) -> None:
    """Test play_media, device is already playing and can't stop."""
    dmr_device_mock.can_stop = False
    dmr_device_mock.transport_state = TransportState.PLAYING
    await hass.services.async_call(
        MP_DOMAIN,
        mp_const.SERVICE_PLAY_MEDIA,
        {
            ATTR_ENTITY_ID: mock_entity_id,
            mp_const.ATTR_MEDIA_CONTENT_TYPE: mp_const.MEDIA_TYPE_MUSIC,
            mp_const.ATTR_MEDIA_CONTENT_ID: "http://192.88.99.20:8200/MediaItems/17621.mp3",
            mp_const.ATTR_MEDIA_ENQUEUE: False,
        },
        blocking=True,
    )

    dmr_device_mock.construct_play_media_metadata.assert_awaited_once_with(
        media_url="http://192.88.99.20:8200/MediaItems/17621.mp3",
        media_title="Home Assistant",
        override_upnp_class="object.item.audioItem.musicTrack",
        meta_data={},
    )
    dmr_device_mock.async_stop.assert_not_awaited()
    dmr_device_mock.async_set_transport_uri.assert_awaited_once_with(
        "http://192.88.99.20:8200/MediaItems/17621.mp3", "Home Assistant", ANY
    )
    dmr_device_mock.async_wait_for_can_play.assert_not_awaited()
    dmr_device_mock.async_play.assert_not_awaited()


async def test_play_media_no_autoplay(
    hass: HomeAssistant, dmr_device_mock: Mock, mock_entity_id: str
) -> None:
    """Test play_media with autoplay=False."""
    # play_media performs a few calls to the device for setup and play
    dmr_device_mock.can_stop = True
    dmr_device_mock.transport_state = TransportState.STOPPED
    await hass.services.async_call(
        MP_DOMAIN,
        mp_const.SERVICE_PLAY_MEDIA,
        {
            ATTR_ENTITY_ID: mock_entity_id,
            mp_const.ATTR_MEDIA_CONTENT_TYPE: mp_const.MEDIA_TYPE_MUSIC,
            mp_const.ATTR_MEDIA_CONTENT_ID: "http://192.88.99.20:8200/MediaItems/17621.mp3",
            mp_const.ATTR_MEDIA_ENQUEUE: False,
            mp_const.ATTR_MEDIA_EXTRA: {"autoplay": False},
        },
        blocking=True,
    )

    dmr_device_mock.construct_play_media_metadata.assert_awaited_once_with(
        media_url="http://192.88.99.20:8200/MediaItems/17621.mp3",
        media_title="Home Assistant",
        override_upnp_class="object.item.audioItem.musicTrack",
        meta_data={},
    )
    dmr_device_mock.async_stop.assert_awaited_once_with()
    dmr_device_mock.async_set_transport_uri.assert_awaited_once_with(
        "http://192.88.99.20:8200/MediaItems/17621.mp3", "Home Assistant", ANY
    )
    dmr_device_mock.async_wait_for_can_play.assert_not_awaited()
    dmr_device_mock.async_play.assert_not_awaited()


async def test_play_media_metadata(
    hass: HomeAssistant, dmr_device_mock: Mock, mock_entity_id: str
) -> None:
    """Test play_media constructs useful metadata from user params."""
    await hass.services.async_call(
        MP_DOMAIN,
        mp_const.SERVICE_PLAY_MEDIA,
        {
            ATTR_ENTITY_ID: mock_entity_id,
            mp_const.ATTR_MEDIA_CONTENT_TYPE: mp_const.MEDIA_TYPE_MUSIC,
            mp_const.ATTR_MEDIA_CONTENT_ID: "http://192.88.99.20:8200/MediaItems/17621.mp3",
            mp_const.ATTR_MEDIA_ENQUEUE: False,
            mp_const.ATTR_MEDIA_EXTRA: {
                "title": "Mock song",
                "thumb": "http://192.88.99.20:8200/MediaItems/17621.jpg",
                "metadata": {"artist": "Mock artist", "album": "Mock album"},
            },
        },
        blocking=True,
    )

    dmr_device_mock.construct_play_media_metadata.assert_awaited_once_with(
        media_url="http://192.88.99.20:8200/MediaItems/17621.mp3",
        media_title="Mock song",
        override_upnp_class="object.item.audioItem.musicTrack",
        meta_data={
            "artist": "Mock artist",
            "album": "Mock album",
            "album_art_uri": "http://192.88.99.20:8200/MediaItems/17621.jpg",
        },
    )

    # Check again for a different media type
    dmr_device_mock.construct_play_media_metadata.reset_mock()
    await hass.services.async_call(
        MP_DOMAIN,
        mp_const.SERVICE_PLAY_MEDIA,
        {
            ATTR_ENTITY_ID: mock_entity_id,
            mp_const.ATTR_MEDIA_CONTENT_TYPE: mp_const.MEDIA_TYPE_TVSHOW,
            mp_const.ATTR_MEDIA_CONTENT_ID: "http://192.88.99.20:8200/MediaItems/123.mkv",
            mp_const.ATTR_MEDIA_ENQUEUE: False,
            mp_const.ATTR_MEDIA_EXTRA: {
                "title": "Mock show",
                "metadata": {"season": 1, "episode": 12},
            },
        },
        blocking=True,
    )

    dmr_device_mock.construct_play_media_metadata.assert_awaited_once_with(
        media_url="http://192.88.99.20:8200/MediaItems/123.mkv",
        media_title="Mock show",
        override_upnp_class="object.item.videoItem.videoBroadcast",
        meta_data={"episodeSeason": 1, "episodeNumber": 12},
    )


async def test_shuffle_repeat_modes(
    hass: HomeAssistant, dmr_device_mock: Mock, mock_entity_id: str
) -> None:
    """Test setting repeat and shuffle modes."""
    # Test shuffle with all variations of existing play mode
    dmr_device_mock.valid_play_modes = {mode.value for mode in PlayMode}
    for init_mode, shuffle_set, expect_mode in [
        (PlayMode.NORMAL, False, PlayMode.NORMAL),
        (PlayMode.SHUFFLE, False, PlayMode.NORMAL),
        (PlayMode.REPEAT_ONE, False, PlayMode.REPEAT_ONE),
        (PlayMode.REPEAT_ALL, False, PlayMode.REPEAT_ALL),
        (PlayMode.RANDOM, False, PlayMode.REPEAT_ALL),
        (PlayMode.NORMAL, True, PlayMode.SHUFFLE),
        (PlayMode.SHUFFLE, True, PlayMode.SHUFFLE),
        (PlayMode.REPEAT_ONE, True, PlayMode.RANDOM),
        (PlayMode.REPEAT_ALL, True, PlayMode.RANDOM),
        (PlayMode.RANDOM, True, PlayMode.RANDOM),
    ]:
        dmr_device_mock.play_mode = init_mode
        await hass.services.async_call(
            MP_DOMAIN,
            ha_const.SERVICE_SHUFFLE_SET,
            {ATTR_ENTITY_ID: mock_entity_id, mp_const.ATTR_MEDIA_SHUFFLE: shuffle_set},
            blocking=True,
        )
        dmr_device_mock.async_set_play_mode.assert_awaited_with(expect_mode)

    # Test repeat with all variations of existing play mode
    for init_mode, repeat_set, expect_mode in [
        (PlayMode.NORMAL, mp_const.REPEAT_MODE_OFF, PlayMode.NORMAL),
        (PlayMode.SHUFFLE, mp_const.REPEAT_MODE_OFF, PlayMode.SHUFFLE),
        (PlayMode.REPEAT_ONE, mp_const.REPEAT_MODE_OFF, PlayMode.NORMAL),
        (PlayMode.REPEAT_ALL, mp_const.REPEAT_MODE_OFF, PlayMode.NORMAL),
        (PlayMode.RANDOM, mp_const.REPEAT_MODE_OFF, PlayMode.SHUFFLE),
        (PlayMode.NORMAL, mp_const.REPEAT_MODE_ONE, PlayMode.REPEAT_ONE),
        (PlayMode.SHUFFLE, mp_const.REPEAT_MODE_ONE, PlayMode.REPEAT_ONE),
        (PlayMode.REPEAT_ONE, mp_const.REPEAT_MODE_ONE, PlayMode.REPEAT_ONE),
        (PlayMode.REPEAT_ALL, mp_const.REPEAT_MODE_ONE, PlayMode.REPEAT_ONE),
        (PlayMode.RANDOM, mp_const.REPEAT_MODE_ONE, PlayMode.REPEAT_ONE),
        (PlayMode.NORMAL, mp_const.REPEAT_MODE_ALL, PlayMode.REPEAT_ALL),
        (PlayMode.SHUFFLE, mp_const.REPEAT_MODE_ALL, PlayMode.RANDOM),
        (PlayMode.REPEAT_ONE, mp_const.REPEAT_MODE_ALL, PlayMode.REPEAT_ALL),
        (PlayMode.REPEAT_ALL, mp_const.REPEAT_MODE_ALL, PlayMode.REPEAT_ALL),
        (PlayMode.RANDOM, mp_const.REPEAT_MODE_ALL, PlayMode.RANDOM),
    ]:
        dmr_device_mock.play_mode = init_mode
        await hass.services.async_call(
            MP_DOMAIN,
            ha_const.SERVICE_REPEAT_SET,
            {ATTR_ENTITY_ID: mock_entity_id, mp_const.ATTR_MEDIA_REPEAT: repeat_set},
            blocking=True,
        )
        dmr_device_mock.async_set_play_mode.assert_awaited_with(expect_mode)

    # Test shuffle when the device doesn't support the desired play mode.
    # Trying to go from RANDOM -> REPEAT_MODE_ALL, but nothing in the list is supported.
    dmr_device_mock.async_set_play_mode.reset_mock()
    dmr_device_mock.play_mode = PlayMode.RANDOM
    dmr_device_mock.valid_play_modes = {PlayMode.SHUFFLE, PlayMode.RANDOM}
    await hass.services.async_call(
        MP_DOMAIN,
        ha_const.SERVICE_SHUFFLE_SET,
        {ATTR_ENTITY_ID: mock_entity_id, mp_const.ATTR_MEDIA_SHUFFLE: False},
        blocking=True,
    )
    dmr_device_mock.async_set_play_mode.assert_not_awaited()

    # Test repeat when the device doesn't support the desired play mode.
    # Trying to go from RANDOM -> SHUFFLE, but nothing in the list is supported.
    dmr_device_mock.async_set_play_mode.reset_mock()
    dmr_device_mock.play_mode = PlayMode.RANDOM
    dmr_device_mock.valid_play_modes = {PlayMode.REPEAT_ONE, PlayMode.REPEAT_ALL}
    await hass.services.async_call(
        MP_DOMAIN,
        ha_const.SERVICE_REPEAT_SET,
        {
            ATTR_ENTITY_ID: mock_entity_id,
            mp_const.ATTR_MEDIA_REPEAT: mp_const.REPEAT_MODE_OFF,
        },
        blocking=True,
    )
    dmr_device_mock.async_set_play_mode.assert_not_awaited()


async def test_playback_update_state(
    hass: HomeAssistant, dmr_device_mock: Mock, mock_entity_id: str
) -> None:
    """Test starting or pausing playback causes the state to be refreshed.

    This is necessary for responsive updates of the current track position and
    total track time.
    """
    on_event = dmr_device_mock.on_event
    mock_service = Mock(UpnpService)
    mock_service.service_id = "urn:upnp-org:serviceId:AVTransport"
    mock_state_variable = Mock(UpnpStateVariable)
    mock_state_variable.name = "TransportState"

    # Event update that device has started playing, device should get polled
    mock_state_variable.value = TransportState.PLAYING
    on_event(mock_service, [mock_state_variable])
    await hass.async_block_till_done()
    dmr_device_mock.async_update.assert_awaited_once_with(do_ping=False)

    # Event update that device has paused playing, device should get polled
    dmr_device_mock.async_update.reset_mock()
    mock_state_variable.value = TransportState.PAUSED_PLAYBACK
    on_event(mock_service, [mock_state_variable])
    await hass.async_block_till_done()
    dmr_device_mock.async_update.assert_awaited_once_with(do_ping=False)

    # Different service shouldn't do anything
    dmr_device_mock.async_update.reset_mock()
    mock_service.service_id = "urn:upnp-org:serviceId:RenderingControl"
    on_event(mock_service, [mock_state_variable])
    await hass.async_block_till_done()
    dmr_device_mock.async_update.assert_not_awaited()


async def test_unavailable_device(
    hass: HomeAssistant,
    domain_data_mock: Mock,
    ssdp_scanner_mock: Mock,
    config_entry_mock: MockConfigEntry,
) -> None:
    """Test a DlnaDmrEntity with out a connected DmrDevice."""
    # Cause connection attempts to fail
    domain_data_mock.upnp_factory.async_create_device.side_effect = UpnpConnectionError

    with patch(
        "homeassistant.components.dlna_dmr.media_player.DmrDevice", autospec=True
    ) as dmr_device_constructor_mock:
        mock_entity_id = await setup_mock_component(hass, config_entry_mock)
        mock_state = hass.states.get(mock_entity_id)
        assert mock_state is not None

        # Check device is not created
        dmr_device_constructor_mock.assert_not_called()

    # Check attempt was made to create a device from the supplied URL
    domain_data_mock.upnp_factory.async_create_device.assert_awaited_once_with(
        MOCK_DEVICE_LOCATION
    )
    # Check event notifiers are not acquired
    domain_data_mock.async_get_event_notifier.assert_not_called()
    # Check SSDP notifications are registered
    ssdp_scanner_mock.async_register_callback.assert_any_call(
        ANY, {"USN": MOCK_DEVICE_USN}
    )
    ssdp_scanner_mock.async_register_callback.assert_any_call(
        ANY, {"_udn": MOCK_DEVICE_UDN, "NTS": "ssdp:byebye"}
    )
    # Quick check of the state to verify the entity has no connected DmrDevice
    assert mock_state.state == ha_const.STATE_UNAVAILABLE
    # Check the name matches that supplied
    assert mock_state.name == MOCK_DEVICE_NAME

    # Check that an update does not attempt to contact the device because
    # poll_availability is False
    domain_data_mock.upnp_factory.async_create_device.reset_mock()
    await async_update_entity(hass, mock_entity_id)
    domain_data_mock.upnp_factory.async_create_device.assert_not_called()

    # Now set poll_availability = True and expect construction attempt
    hass.config_entries.async_update_entry(
        config_entry_mock, options={CONF_POLL_AVAILABILITY: True}
    )
    await async_update_entity(hass, mock_entity_id)
    domain_data_mock.upnp_factory.async_create_device.assert_awaited_once_with(
        MOCK_DEVICE_LOCATION
    )

    # Check attributes are unavailable
    attrs = mock_state.attributes
    for attr in ATTR_TO_PROPERTY:
        assert attr not in attrs

    assert attrs[ha_const.ATTR_FRIENDLY_NAME] == MOCK_DEVICE_NAME
    assert attrs[ha_const.ATTR_SUPPORTED_FEATURES] == 0
    assert mp_const.ATTR_SOUND_MODE_LIST not in attrs

    # Check service calls do nothing
    SERVICES: list[tuple[str, dict]] = [
        (ha_const.SERVICE_VOLUME_SET, {mp_const.ATTR_MEDIA_VOLUME_LEVEL: 0.80}),
        (ha_const.SERVICE_VOLUME_MUTE, {mp_const.ATTR_MEDIA_VOLUME_MUTED: True}),
        (ha_const.SERVICE_MEDIA_PAUSE, {}),
        (ha_const.SERVICE_MEDIA_PLAY, {}),
        (ha_const.SERVICE_MEDIA_STOP, {}),
        (ha_const.SERVICE_MEDIA_NEXT_TRACK, {}),
        (ha_const.SERVICE_MEDIA_PREVIOUS_TRACK, {}),
        (ha_const.SERVICE_MEDIA_SEEK, {mp_const.ATTR_MEDIA_SEEK_POSITION: 33}),
        (
            mp_const.SERVICE_PLAY_MEDIA,
            {
                mp_const.ATTR_MEDIA_CONTENT_TYPE: mp_const.MEDIA_TYPE_MUSIC,
                mp_const.ATTR_MEDIA_CONTENT_ID: "http://192.88.99.20:8200/MediaItems/17621.mp3",
                mp_const.ATTR_MEDIA_ENQUEUE: False,
            },
        ),
        (mp_const.SERVICE_SELECT_SOUND_MODE, {mp_const.ATTR_SOUND_MODE: "Default"}),
        (ha_const.SERVICE_SHUFFLE_SET, {mp_const.ATTR_MEDIA_SHUFFLE: True}),
        (ha_const.SERVICE_REPEAT_SET, {mp_const.ATTR_MEDIA_REPEAT: "all"}),
    ]
    for service, data in SERVICES:
        await hass.services.async_call(
            MP_DOMAIN,
            service,
            {ATTR_ENTITY_ID: mock_entity_id, **data},
            blocking=True,
        )

    # Check hass device information has not been filled in yet
    dev_reg = async_get_dr(hass)
    device = dev_reg.async_get_device(identifiers={(DLNA_DOMAIN, MOCK_DEVICE_UDN)})
    assert device is None

    # Unload config entry to clean up
    assert await hass.config_entries.async_remove(config_entry_mock.entry_id) == {
        "require_restart": False
    }

    # Confirm SSDP notifications unregistered
    assert ssdp_scanner_mock.async_register_callback.return_value.call_count == 2

    # Check event notifiers are not released
    domain_data_mock.async_release_event_notifier.assert_not_called()

    # Confirm the entity is still unavailable
    mock_state = hass.states.get(mock_entity_id)
    assert mock_state is not None
    assert mock_state.state == ha_const.STATE_UNAVAILABLE


async def test_become_available(
    hass: HomeAssistant,
    domain_data_mock: Mock,
    ssdp_scanner_mock: Mock,
    config_entry_mock: MockConfigEntry,
    dmr_device_mock: Mock,
) -> None:
    """Test a device becoming available after the entity is constructed."""
    # Cause connection attempts to fail before adding entity
    domain_data_mock.upnp_factory.async_create_device.side_effect = UpnpConnectionError
    mock_entity_id = await setup_mock_component(hass, config_entry_mock)
    mock_state = hass.states.get(mock_entity_id)
    assert mock_state is not None
    assert mock_state.state == ha_const.STATE_UNAVAILABLE

    # Check hass device information has not been filled in yet
    dev_reg = async_get_dr(hass)
    device = dev_reg.async_get_device(identifiers={(DLNA_DOMAIN, MOCK_DEVICE_UDN)})
    assert device is None

    # Mock device is now available.
    domain_data_mock.upnp_factory.async_create_device.side_effect = None
    domain_data_mock.upnp_factory.async_create_device.reset_mock()

    # Send an SSDP notification from the now alive device
    ssdp_callback = ssdp_scanner_mock.async_register_callback.call_args.args[0]
    await ssdp_callback(
        ssdp.SsdpServiceInfo(
            ssdp_usn=MOCK_DEVICE_USN,
            ssdp_location=NEW_DEVICE_LOCATION,
            ssdp_st=MOCK_DEVICE_ST,
            upnp={},
        ),
        ssdp.SsdpChange.ALIVE,
    )
    await hass.async_block_till_done()

    # Check device was created from the supplied URL
    domain_data_mock.upnp_factory.async_create_device.assert_awaited_once_with(
        NEW_DEVICE_LOCATION
    )
    # Check event notifiers are acquired
    domain_data_mock.async_get_event_notifier.assert_awaited_once_with(
        EventListenAddr(LOCAL_IP, 0, None), hass
    )
    # Check UPnP services are subscribed
    dmr_device_mock.async_subscribe_services.assert_awaited_once_with(
        auto_resubscribe=True
    )
    assert dmr_device_mock.on_event is not None
    # Quick check of the state to verify the entity has a connected DmrDevice
    mock_state = hass.states.get(mock_entity_id)
    assert mock_state is not None
    assert mock_state.state == ha_const.STATE_IDLE
    # Check hass device information is now filled in
    dev_reg = async_get_dr(hass)
    device = dev_reg.async_get_device(identifiers={(DLNA_DOMAIN, MOCK_DEVICE_UDN)})
    assert device is not None
    assert device.manufacturer == "device_manufacturer"
    assert device.model == "device_model_name"
    assert device.name == "device_name"

    # Unload config entry to clean up
    assert await hass.config_entries.async_remove(config_entry_mock.entry_id) == {
        "require_restart": False
    }

    # Confirm SSDP notifications unregistered
    assert ssdp_scanner_mock.async_register_callback.return_value.call_count == 2

    # Confirm the entity has disconnected from the device
    domain_data_mock.async_release_event_notifier.assert_awaited_once()
    dmr_device_mock.async_unsubscribe_services.assert_awaited_once()
    assert dmr_device_mock.on_event is None
    mock_state = hass.states.get(mock_entity_id)
    assert mock_state is not None
    assert mock_state.state == ha_const.STATE_UNAVAILABLE


async def test_alive_but_gone(
    hass: HomeAssistant,
    domain_data_mock: Mock,
    ssdp_scanner_mock: Mock,
    mock_disconnected_entity_id: str,
) -> None:
    """Test a device sending an SSDP alive announcement, but not being connectable."""
    domain_data_mock.upnp_factory.async_create_device.side_effect = UpnpError

    # Send an SSDP notification from the still missing device
    ssdp_callback = ssdp_scanner_mock.async_register_callback.call_args.args[0]
    await ssdp_callback(
        ssdp.SsdpServiceInfo(
            ssdp_usn=MOCK_DEVICE_USN,
            ssdp_location=NEW_DEVICE_LOCATION,
            ssdp_st=MOCK_DEVICE_ST,
            upnp={},
        ),
        ssdp.SsdpChange.ALIVE,
    )
    await hass.async_block_till_done()

    # Device should still be unavailable
    mock_state = hass.states.get(mock_disconnected_entity_id)
    assert mock_state is not None
    assert mock_state.state == ha_const.STATE_UNAVAILABLE


async def test_multiple_ssdp_alive(
    hass: HomeAssistant,
    domain_data_mock: Mock,
    ssdp_scanner_mock: Mock,
    mock_disconnected_entity_id: str,
) -> None:
    """Test multiple SSDP alive notifications is ok, only connects to device once."""
    domain_data_mock.upnp_factory.async_create_device.reset_mock()

    # Contacting the device takes long enough that 2 simultaneous attempts could be made
    async def create_device_delayed(_location):
        """Delay before continuing with async_create_device.

        This gives a chance for parallel calls to `_device_connect` to occur.
        """
        await asyncio.sleep(0.1)
        return DEFAULT

    domain_data_mock.upnp_factory.async_create_device.side_effect = (
        create_device_delayed
    )

    # Send two SSDP notifications with the new device URL
    ssdp_callback = ssdp_scanner_mock.async_register_callback.call_args.args[0]
    await ssdp_callback(
        ssdp.SsdpServiceInfo(
            ssdp_usn=MOCK_DEVICE_USN,
            ssdp_location=NEW_DEVICE_LOCATION,
            ssdp_st=MOCK_DEVICE_ST,
            upnp={},
        ),
        ssdp.SsdpChange.ALIVE,
    )
    await ssdp_callback(
        ssdp.SsdpServiceInfo(
            ssdp_usn=MOCK_DEVICE_USN,
            ssdp_location=NEW_DEVICE_LOCATION,
            ssdp_st=MOCK_DEVICE_ST,
            upnp={},
        ),
        ssdp.SsdpChange.ALIVE,
    )
    await hass.async_block_till_done()

    # Check device is contacted exactly once
    domain_data_mock.upnp_factory.async_create_device.assert_awaited_once_with(
        NEW_DEVICE_LOCATION
    )

    # Device should be available
    mock_state = hass.states.get(mock_disconnected_entity_id)
    assert mock_state is not None
    assert mock_state.state == media_player.STATE_IDLE


async def test_ssdp_byebye(
    hass: HomeAssistant,
    ssdp_scanner_mock: Mock,
    mock_entity_id: str,
    dmr_device_mock: Mock,
) -> None:
    """Test device is disconnected when byebye is received."""
    # First byebye will cause a disconnect
    ssdp_callback = ssdp_scanner_mock.async_register_callback.call_args.args[0]
    await ssdp_callback(
        ssdp.SsdpServiceInfo(
            ssdp_usn=MOCK_DEVICE_USN,
            ssdp_udn=MOCK_DEVICE_UDN,
            ssdp_headers={"NTS": "ssdp:byebye"},
            ssdp_st=MOCK_DEVICE_ST,
            upnp={},
        ),
        ssdp.SsdpChange.BYEBYE,
    )

    dmr_device_mock.async_unsubscribe_services.assert_awaited_once()

    # Device should be gone
    mock_state = hass.states.get(mock_entity_id)
    assert mock_state is not None
    assert mock_state.state == ha_const.STATE_UNAVAILABLE

    # Second byebye will do nothing
    await ssdp_callback(
        ssdp.SsdpServiceInfo(
            ssdp_usn=MOCK_DEVICE_USN,
            ssdp_udn=MOCK_DEVICE_UDN,
            ssdp_headers={"NTS": "ssdp:byebye"},
            ssdp_st=MOCK_DEVICE_ST,
            upnp={},
        ),
        ssdp.SsdpChange.BYEBYE,
    )

    dmr_device_mock.async_unsubscribe_services.assert_awaited_once()


async def test_ssdp_update_seen_bootid(
    hass: HomeAssistant,
    domain_data_mock: Mock,
    ssdp_scanner_mock: Mock,
    mock_disconnected_entity_id: str,
    dmr_device_mock: Mock,
) -> None:
    """Test device does not reconnect when it gets ssdp:update with next bootid."""
    # Start with a disconnected device
    entity_id = mock_disconnected_entity_id
    mock_state = hass.states.get(entity_id)
    assert mock_state is not None
    assert mock_state.state == ha_const.STATE_UNAVAILABLE

    # "Reconnect" the device
    domain_data_mock.upnp_factory.async_create_device.side_effect = None

    # Send SSDP alive with boot ID
    ssdp_callback = ssdp_scanner_mock.async_register_callback.call_args.args[0]
    await ssdp_callback(
        ssdp.SsdpServiceInfo(
            ssdp_usn=MOCK_DEVICE_USN,
            ssdp_location=MOCK_DEVICE_LOCATION,
            ssdp_headers={ssdp.ATTR_SSDP_BOOTID: "1"},
            ssdp_st=MOCK_DEVICE_ST,
            upnp={},
        ),
        ssdp.SsdpChange.ALIVE,
    )
    await hass.async_block_till_done()

    # Send SSDP update with next boot ID
    await ssdp_callback(
        ssdp.SsdpServiceInfo(
            ssdp_usn=MOCK_DEVICE_USN,
            ssdp_udn=MOCK_DEVICE_UDN,
            ssdp_headers={
                "NTS": "ssdp:update",
                ssdp.ATTR_SSDP_BOOTID: "1",
                ssdp.ATTR_SSDP_NEXTBOOTID: "2",
            },
            ssdp_st=MOCK_DEVICE_ST,
            upnp={},
        ),
        ssdp.SsdpChange.UPDATE,
    )
    await hass.async_block_till_done()

    # Device was not reconnected, even with a new boot ID
    mock_state = hass.states.get(entity_id)
    assert mock_state is not None
    assert mock_state.state == ha_const.STATE_IDLE

    assert dmr_device_mock.async_unsubscribe_services.await_count == 0
    assert dmr_device_mock.async_subscribe_services.await_count == 1

    # Send SSDP update with same next boot ID, again
    await ssdp_callback(
        ssdp.SsdpServiceInfo(
            ssdp_usn=MOCK_DEVICE_USN,
            ssdp_udn=MOCK_DEVICE_UDN,
            ssdp_headers={
                "NTS": "ssdp:update",
                ssdp.ATTR_SSDP_BOOTID: "1",
                ssdp.ATTR_SSDP_NEXTBOOTID: "2",
            },
            ssdp_st=MOCK_DEVICE_ST,
            upnp={},
        ),
        ssdp.SsdpChange.UPDATE,
    )
    await hass.async_block_till_done()

    # Nothing should change
    mock_state = hass.states.get(entity_id)
    assert mock_state is not None
    assert mock_state.state == ha_const.STATE_IDLE

    assert dmr_device_mock.async_unsubscribe_services.await_count == 0
    assert dmr_device_mock.async_subscribe_services.await_count == 1

    # Send SSDP update with bad next boot ID
    await ssdp_callback(
        ssdp.SsdpServiceInfo(
            ssdp_usn=MOCK_DEVICE_USN,
            ssdp_udn=MOCK_DEVICE_UDN,
            ssdp_headers={
                "NTS": "ssdp:update",
                ssdp.ATTR_SSDP_BOOTID: "2",
                ssdp.ATTR_SSDP_NEXTBOOTID: "7c848375-a106-4bd1-ac3c-8e50427c8e4f",
            },
            ssdp_st=MOCK_DEVICE_ST,
            upnp={},
        ),
        ssdp.SsdpChange.UPDATE,
    )
    await hass.async_block_till_done()

    # Nothing should change
    mock_state = hass.states.get(entity_id)
    assert mock_state is not None
    assert mock_state.state == ha_const.STATE_IDLE

    assert dmr_device_mock.async_unsubscribe_services.await_count == 0
    assert dmr_device_mock.async_subscribe_services.await_count == 1

    # Send a new SSDP alive with the new boot ID, device should not reconnect
    await ssdp_callback(
        ssdp.SsdpServiceInfo(
            ssdp_usn=MOCK_DEVICE_USN,
            ssdp_location=MOCK_DEVICE_LOCATION,
            ssdp_headers={ssdp.ATTR_SSDP_BOOTID: "2"},
            ssdp_st=MOCK_DEVICE_ST,
            upnp={},
        ),
        ssdp.SsdpChange.ALIVE,
    )
    await hass.async_block_till_done()

    mock_state = hass.states.get(entity_id)
    assert mock_state is not None
    assert mock_state.state == ha_const.STATE_IDLE

    assert dmr_device_mock.async_unsubscribe_services.await_count == 0
    assert dmr_device_mock.async_subscribe_services.await_count == 1


async def test_ssdp_update_missed_bootid(
    hass: HomeAssistant,
    domain_data_mock: Mock,
    ssdp_scanner_mock: Mock,
    mock_disconnected_entity_id: str,
    dmr_device_mock: Mock,
) -> None:
    """Test device disconnects when it gets ssdp:update bootid it wasn't expecting."""
    # Start with a disconnected device
    entity_id = mock_disconnected_entity_id
    mock_state = hass.states.get(entity_id)
    assert mock_state is not None
    assert mock_state.state == ha_const.STATE_UNAVAILABLE

    # "Reconnect" the device
    domain_data_mock.upnp_factory.async_create_device.side_effect = None

    # Send SSDP alive with boot ID
    ssdp_callback = ssdp_scanner_mock.async_register_callback.call_args.args[0]
    await ssdp_callback(
        ssdp.SsdpServiceInfo(
            ssdp_usn=MOCK_DEVICE_USN,
            ssdp_location=MOCK_DEVICE_LOCATION,
            ssdp_headers={ssdp.ATTR_SSDP_BOOTID: "1"},
            ssdp_st=MOCK_DEVICE_ST,
            upnp={},
        ),
        ssdp.SsdpChange.ALIVE,
    )
    await hass.async_block_till_done()

    # Send SSDP update with skipped boot ID (not previously seen)
    await ssdp_callback(
        ssdp.SsdpServiceInfo(
            ssdp_usn=MOCK_DEVICE_USN,
            ssdp_udn=MOCK_DEVICE_UDN,
            ssdp_headers={
                "NTS": "ssdp:update",
                ssdp.ATTR_SSDP_BOOTID: "2",
                ssdp.ATTR_SSDP_NEXTBOOTID: "3",
            },
            ssdp_st=MOCK_DEVICE_ST,
            upnp={},
        ),
        ssdp.SsdpChange.UPDATE,
    )
    await hass.async_block_till_done()

    # Device should not reconnect yet
    mock_state = hass.states.get(entity_id)
    assert mock_state is not None
    assert mock_state.state == ha_const.STATE_IDLE

    assert dmr_device_mock.async_unsubscribe_services.await_count == 0
    assert dmr_device_mock.async_subscribe_services.await_count == 1

    # Send a new SSDP alive with the new boot ID, device should reconnect
    await ssdp_callback(
        ssdp.SsdpServiceInfo(
            ssdp_usn=MOCK_DEVICE_USN,
            ssdp_location=MOCK_DEVICE_LOCATION,
            ssdp_headers={ssdp.ATTR_SSDP_BOOTID: "3"},
            ssdp_st=MOCK_DEVICE_ST,
            upnp={},
        ),
        ssdp.SsdpChange.ALIVE,
    )
    await hass.async_block_till_done()

    mock_state = hass.states.get(entity_id)
    assert mock_state is not None
    assert mock_state.state == ha_const.STATE_IDLE

    assert dmr_device_mock.async_unsubscribe_services.await_count == 1
    assert dmr_device_mock.async_subscribe_services.await_count == 2


async def test_ssdp_bootid(
    hass: HomeAssistant,
    domain_data_mock: Mock,
    ssdp_scanner_mock: Mock,
    mock_disconnected_entity_id: str,
    dmr_device_mock: Mock,
) -> None:
    """Test an alive with a new BOOTID.UPNP.ORG header causes a reconnect."""
    # Start with a disconnected device
    entity_id = mock_disconnected_entity_id
    mock_state = hass.states.get(entity_id)
    assert mock_state is not None
    assert mock_state.state == ha_const.STATE_UNAVAILABLE

    # "Reconnect" the device
    domain_data_mock.upnp_factory.async_create_device.side_effect = None

    # Send SSDP alive with boot ID
    ssdp_callback = ssdp_scanner_mock.async_register_callback.call_args.args[0]
    await ssdp_callback(
        ssdp.SsdpServiceInfo(
            ssdp_usn=MOCK_DEVICE_USN,
            ssdp_location=MOCK_DEVICE_LOCATION,
            ssdp_headers={ssdp.ATTR_SSDP_BOOTID: "1"},
            ssdp_st=MOCK_DEVICE_ST,
            upnp={},
        ),
        ssdp.SsdpChange.ALIVE,
    )
    await hass.async_block_till_done()

    mock_state = hass.states.get(entity_id)
    assert mock_state is not None
    assert mock_state.state == ha_const.STATE_IDLE

    assert dmr_device_mock.async_subscribe_services.call_count == 1
    assert dmr_device_mock.async_unsubscribe_services.call_count == 0

    # Send SSDP alive with same boot ID, nothing should happen
    await ssdp_callback(
        ssdp.SsdpServiceInfo(
            ssdp_usn=MOCK_DEVICE_USN,
            ssdp_location=MOCK_DEVICE_LOCATION,
            ssdp_headers={ssdp.ATTR_SSDP_BOOTID: "1"},
            ssdp_st=MOCK_DEVICE_ST,
            upnp={},
        ),
        ssdp.SsdpChange.ALIVE,
    )
    await hass.async_block_till_done()

    mock_state = hass.states.get(entity_id)
    assert mock_state is not None
    assert mock_state.state == ha_const.STATE_IDLE

    assert dmr_device_mock.async_subscribe_services.call_count == 1
    assert dmr_device_mock.async_unsubscribe_services.call_count == 0

    # Send a new SSDP alive with an incremented boot ID, device should be dis/reconnected
    await ssdp_callback(
        ssdp.SsdpServiceInfo(
            ssdp_usn=MOCK_DEVICE_USN,
            ssdp_location=MOCK_DEVICE_LOCATION,
            ssdp_headers={ssdp.ATTR_SSDP_BOOTID: "2"},
            ssdp_st=MOCK_DEVICE_ST,
            upnp={},
        ),
        ssdp.SsdpChange.ALIVE,
    )
    await hass.async_block_till_done()

    mock_state = hass.states.get(entity_id)
    assert mock_state is not None
    assert mock_state.state == ha_const.STATE_IDLE

    assert dmr_device_mock.async_subscribe_services.call_count == 2
    assert dmr_device_mock.async_unsubscribe_services.call_count == 1


async def test_become_unavailable(
    hass: HomeAssistant,
    mock_entity_id: str,
    dmr_device_mock: Mock,
) -> None:
    """Test a device becoming unavailable."""
    # Check async_update currently works
    await async_update_entity(hass, mock_entity_id)
    dmr_device_mock.async_update.assert_called_with(do_ping=False)

    # Now break the network connection and try to contact the device
    dmr_device_mock.async_set_volume_level.side_effect = UpnpConnectionError
    dmr_device_mock.async_update.reset_mock()

    # Interface service calls should flag that the device is unavailable, but
    # not disconnect it immediately
    await hass.services.async_call(
        MP_DOMAIN,
        ha_const.SERVICE_VOLUME_SET,
        {ATTR_ENTITY_ID: mock_entity_id, mp_const.ATTR_MEDIA_VOLUME_LEVEL: 0.80},
        blocking=True,
    )

    mock_state = hass.states.get(mock_entity_id)
    assert mock_state is not None
    assert mock_state.state == ha_const.STATE_IDLE

    # With a working connection, the state should be restored
    await async_update_entity(hass, mock_entity_id)
    dmr_device_mock.async_update.assert_any_call(do_ping=True)
    mock_state = hass.states.get(mock_entity_id)
    assert mock_state is not None
    assert mock_state.state == ha_const.STATE_IDLE

    # Break the service again, and the connection too. An update will cause the
    # device to be disconnected
    dmr_device_mock.async_update.reset_mock()
    dmr_device_mock.async_update.side_effect = UpnpConnectionError

    await hass.services.async_call(
        MP_DOMAIN,
        ha_const.SERVICE_VOLUME_SET,
        {ATTR_ENTITY_ID: mock_entity_id, mp_const.ATTR_MEDIA_VOLUME_LEVEL: 0.80},
        blocking=True,
    )
    await async_update_entity(hass, mock_entity_id)
    dmr_device_mock.async_update.assert_called_with(do_ping=True)
    mock_state = hass.states.get(mock_entity_id)
    assert mock_state is not None
    assert mock_state.state == ha_const.STATE_UNAVAILABLE


async def test_poll_availability(
    hass: HomeAssistant,
    domain_data_mock: Mock,
    config_entry_mock: MockConfigEntry,
    dmr_device_mock: Mock,
) -> None:
    """Test device becomes available and noticed via poll_availability."""
    # Start with a disconnected device and poll_availability=True
    domain_data_mock.upnp_factory.async_create_device.side_effect = UpnpConnectionError
    config_entry_mock.options = MappingProxyType(
        {
            CONF_POLL_AVAILABILITY: True,
        }
    )
    mock_entity_id = await setup_mock_component(hass, config_entry_mock)
    mock_state = hass.states.get(mock_entity_id)
    assert mock_state is not None
    assert mock_state.state == ha_const.STATE_UNAVAILABLE

    # Check that an update will poll the device for availability
    domain_data_mock.upnp_factory.async_create_device.reset_mock()
    await async_update_entity(hass, mock_entity_id)
    domain_data_mock.upnp_factory.async_create_device.assert_awaited_once_with(
        MOCK_DEVICE_LOCATION
    )

    mock_state = hass.states.get(mock_entity_id)
    assert mock_state is not None
    assert mock_state.state == ha_const.STATE_UNAVAILABLE

    # "Reconnect" the device
    domain_data_mock.upnp_factory.async_create_device.side_effect = None

    # Check that an update will notice the device and connect to it
    domain_data_mock.upnp_factory.async_create_device.reset_mock()
    await async_update_entity(hass, mock_entity_id)
    domain_data_mock.upnp_factory.async_create_device.assert_awaited_once_with(
        MOCK_DEVICE_LOCATION
    )

    mock_state = hass.states.get(mock_entity_id)
    assert mock_state is not None
    assert mock_state.state == ha_const.STATE_IDLE

    # Clean up
    assert await hass.config_entries.async_remove(config_entry_mock.entry_id) == {
        "require_restart": False
    }


async def test_disappearing_device(
    hass: HomeAssistant,
    mock_disconnected_entity_id: str,
) -> None:
    """Test attribute update or service call as device disappears.

    Normally HA will check if the entity is available before updating attributes
    or calling a service, but it's possible for the device to go offline in
    between the check and the method call. Here we test by accessing the entity
    directly to skip the availability check.
    """
    # Retrieve entity directly.
    entity: media_player.DlnaDmrEntity = hass.data[MP_DOMAIN].get_entity(
        mock_disconnected_entity_id
    )

    # Test attribute access
    for attr in ATTR_TO_PROPERTY:
        value = getattr(entity, attr)
        assert value is None

    # media_image_url is normally hidden by entity_picture, but we want a direct check
    assert entity.media_image_url is None

    # Check attributes that are normally pre-checked
    assert entity.sound_mode_list is None

    # Test service calls
    await entity.async_set_volume_level(0.1)
    await entity.async_mute_volume(True)
    await entity.async_media_pause()
    await entity.async_media_play()
    await entity.async_media_stop()
    await entity.async_media_seek(22.0)
    await entity.async_play_media("", "")
    await entity.async_media_previous_track()
    await entity.async_media_next_track()
    await entity.async_set_shuffle(True)
    await entity.async_set_repeat(mp_const.REPEAT_MODE_ALL)
    await entity.async_select_sound_mode("Default")


async def test_resubscribe_failure(
    hass: HomeAssistant,
    mock_entity_id: str,
    dmr_device_mock: Mock,
) -> None:
    """Test failure to resubscribe to events notifications causes an update ping."""
    await async_update_entity(hass, mock_entity_id)
    dmr_device_mock.async_update.assert_called_with(do_ping=False)
    dmr_device_mock.async_update.reset_mock()

    on_event = dmr_device_mock.on_event
    mock_service = Mock(UpnpService)
    on_event(mock_service, [])
    await hass.async_block_till_done()

    await async_update_entity(hass, mock_entity_id)
    dmr_device_mock.async_update.assert_called_with(do_ping=True)


async def test_config_update_listen_port(
    hass: HomeAssistant,
    domain_data_mock: Mock,
    config_entry_mock: MockConfigEntry,
    dmr_device_mock: Mock,
    mock_entity_id: str,
) -> None:
    """Test DlnaDmrEntity gets updated by ConfigEntry's CONF_LISTEN_PORT."""
    domain_data_mock.upnp_factory.async_create_device.reset_mock()

    hass.config_entries.async_update_entry(
        config_entry_mock,
        options={
            CONF_LISTEN_PORT: 1234,
        },
    )
    await hass.async_block_till_done()

    # A new event listener with the changed port will be used
    domain_data_mock.async_release_event_notifier.assert_awaited_once_with(
        EventListenAddr(LOCAL_IP, 0, None)
    )
    domain_data_mock.async_get_event_notifier.assert_awaited_with(
        EventListenAddr(LOCAL_IP, 1234, None), hass
    )

    # Device will be reconnected
    domain_data_mock.upnp_factory.async_create_device.assert_awaited_once_with(
        MOCK_DEVICE_LOCATION
    )
    assert dmr_device_mock.async_unsubscribe_services.await_count == 1
    assert dmr_device_mock.async_subscribe_services.await_count == 2

    # Check that its still connected
    mock_state = hass.states.get(mock_entity_id)
    assert mock_state is not None
    assert mock_state.state == ha_const.STATE_IDLE


async def test_config_update_connect_failure(
    hass: HomeAssistant,
    domain_data_mock: Mock,
    config_entry_mock: MockConfigEntry,
    mock_entity_id: str,
) -> None:
    """Test DlnaDmrEntity gracefully handles connect failure after config change."""
    domain_data_mock.upnp_factory.async_create_device.reset_mock()
    domain_data_mock.upnp_factory.async_create_device.side_effect = UpnpError

    hass.config_entries.async_update_entry(
        config_entry_mock,
        options={
            CONF_LISTEN_PORT: 1234,
        },
    )
    await hass.async_block_till_done()

    # Old event listener was released, new event listener was not created
    domain_data_mock.async_release_event_notifier.assert_awaited_once_with(
        EventListenAddr(LOCAL_IP, 0, None)
    )
    domain_data_mock.async_get_event_notifier.assert_awaited_once()

    # There was an attempt to connect to the device
    domain_data_mock.upnp_factory.async_create_device.assert_awaited_once_with(
        MOCK_DEVICE_LOCATION
    )

    # Check that its no longer connected
    mock_state = hass.states.get(mock_entity_id)
    assert mock_state is not None
    assert mock_state.state == ha_const.STATE_UNAVAILABLE


async def test_config_update_callback_url(
    hass: HomeAssistant,
    domain_data_mock: Mock,
    config_entry_mock: MockConfigEntry,
    dmr_device_mock: Mock,
    mock_entity_id: str,
) -> None:
    """Test DlnaDmrEntity gets updated by ConfigEntry's CONF_CALLBACK_URL_OVERRIDE."""
    domain_data_mock.upnp_factory.async_create_device.reset_mock()

    hass.config_entries.async_update_entry(
        config_entry_mock,
        options={
            CONF_CALLBACK_URL_OVERRIDE: "http://www.example.net/notify",
        },
    )
    await hass.async_block_till_done()

    # A new event listener with the changed callback URL will be used
    domain_data_mock.async_release_event_notifier.assert_awaited_once_with(
        EventListenAddr(LOCAL_IP, 0, None)
    )
    domain_data_mock.async_get_event_notifier.assert_awaited_with(
        EventListenAddr(LOCAL_IP, 0, "http://www.example.net/notify"), hass
    )

    # Device will be reconnected
    domain_data_mock.upnp_factory.async_create_device.assert_awaited_once_with(
        MOCK_DEVICE_LOCATION
    )
    assert dmr_device_mock.async_unsubscribe_services.await_count == 1
    assert dmr_device_mock.async_subscribe_services.await_count == 2

    # Check that its still connected
    mock_state = hass.states.get(mock_entity_id)
    assert mock_state is not None
    assert mock_state.state == ha_const.STATE_IDLE


async def test_config_update_poll_availability(
    hass: HomeAssistant,
    domain_data_mock: Mock,
    config_entry_mock: MockConfigEntry,
    dmr_device_mock: Mock,
    mock_entity_id: str,
) -> None:
    """Test DlnaDmrEntity gets updated by ConfigEntry's CONF_POLL_AVAILABILITY."""
    domain_data_mock.upnp_factory.async_create_device.reset_mock()

    # Updates of the device will not ping it yet
    await async_update_entity(hass, mock_entity_id)
    dmr_device_mock.async_update.assert_awaited_with(do_ping=False)

    hass.config_entries.async_update_entry(
        config_entry_mock,
        options={
            CONF_POLL_AVAILABILITY: True,
        },
    )
    await hass.async_block_till_done()

    # Event listeners will not change
    domain_data_mock.async_release_event_notifier.assert_not_awaited()
    domain_data_mock.async_get_event_notifier.assert_awaited_once()

    # Device will not be reconnected
    domain_data_mock.upnp_factory.async_create_device.assert_not_awaited()
    assert dmr_device_mock.async_unsubscribe_services.await_count == 0
    assert dmr_device_mock.async_subscribe_services.await_count == 1

    # Updates of the device will now ping it
    await async_update_entity(hass, mock_entity_id)
    dmr_device_mock.async_update.assert_awaited_with(do_ping=True)

    # Check that its still connected
    mock_state = hass.states.get(mock_entity_id)
    assert mock_state is not None
    assert mock_state.state == ha_const.STATE_IDLE
