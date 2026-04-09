import asyncio
import itertools
import json
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from hummingbot.connector.exchange.yellow_pro import (
    yellow_pro_constants as CONSTANTS,
    yellow_pro_web_utils as web_utils,
)
from hummingbot.connector.exchange.yellow_pro.yellow_pro_auth import YellowProAuth
from hummingbot.core.data_type.user_stream_tracker_data_source import UserStreamTrackerDataSource
from hummingbot.core.web_assistant.connections.data_types import WSJSONRequest
from hummingbot.core.web_assistant.web_assistants_factory import WebAssistantsFactory
from hummingbot.core.web_assistant.ws_assistant import WSAssistant

if TYPE_CHECKING:
    from hummingbot.connector.exchange.yellow_pro.yellow_pro_exchange import YellowProExchange


class YellowProAPIUserStreamDataSource(UserStreamTrackerDataSource):

    def __init__(
            self,
            auth: YellowProAuth,
            app_session_id: str,
            trading_pairs: Optional[List[str]],
            connector: "YellowProExchange",
            api_factory: WebAssistantsFactory,
            domain: str = CONSTANTS.DOMAIN):
        super().__init__()
        self._auth = auth
        self._app_session_id = app_session_id
        self._trading_pairs = trading_pairs or []
        self._connector = connector
        self._api_factory = api_factory
        self._domain = domain
        self._request_id_iterator = itertools.count(start=1)
        self._ws_assistant: Optional[WSAssistant] = None

    async def _connected_websocket_assistant(self) -> WSAssistant:
        ws: WSAssistant = await self._api_factory.get_ws_assistant()
        await ws.connect(
            ws_url=web_utils.wss_url(self._domain),
            ws_headers=self._auth.get_ws_auth_headers(),
            ping_timeout=CONSTANTS.HEARTBEAT_TIME_INTERVAL,
        )
        return ws

    async def _subscribe_channels(self, websocket_assistant: WSAssistant):
        connect_payload = {
            "id": next(self._request_id_iterator),
            "connect": {},
        }
        await websocket_assistant.send(WSJSONRequest(payload=connect_payload))

        subscribe_payload = {
            "id": next(self._request_id_iterator),
            "subscribe": {
                "channel": f"private.{self._app_session_id}",
            },
        }
        await websocket_assistant.send(WSJSONRequest(payload=subscribe_payload))

    def _json_fragments(self, raw_message: str) -> List[Any]:
        fragments: List[Any] = []
        decoder = json.JSONDecoder()
        idx = 0
        length = len(raw_message)
        while idx < length:
            while idx < length and raw_message[idx].isspace():
                idx += 1
            if idx >= length:
                break
            try:
                parsed_obj, next_idx = decoder.raw_decode(raw_message, idx)
            except json.JSONDecodeError as e:
                trimmed = raw_message[idx:idx + 200]
                self.logger().error(f"Failed to decode JSON fragment starting at index {idx}: {e}")
                self.logger().error(f"Fragment content (trimmed): {trimmed}")
                fragments.clear()
                break
            fragments.append(parsed_obj)
            idx = next_idx
        return fragments

    async def _process_event_message(self, event_message: Dict[str, Any], queue: asyncio.Queue):
        if not event_message:
            return
        # Centrifugo server ping: empty dict → reply with empty dict
        if isinstance(event_message, dict) and len(event_message) == 0:
            if self._ws_assistant is not None:
                await self._ws_assistant.send(WSJSONRequest(payload={}))
            return
        if isinstance(event_message, str):
            json_messages = self._json_fragments(event_message)
            if not json_messages:
                return
            for message in json_messages:
                if isinstance(message, dict):
                    await self._process_event_message(event_message=message, queue=queue)
                elif isinstance(message, list):
                    for item in message:
                        if isinstance(item, dict):
                            await self._process_event_message(event_message=item, queue=queue)
                        else:
                            self.logger().error(f"Unsupported JSON item type in list: {type(item)}")
                else:
                    self.logger().error(f"Unsupported JSON fragment type: {type(message)}")
            return
        if not isinstance(event_message, dict):
            self.logger().error(f"Unexpected message type in _process_event_message: {type(event_message)}")
            return
        if "error" in event_message:
            error_payload = event_message.get("error")
            raise IOError(f"YellowPro user stream error: {error_payload}")
        if "connect" in event_message:
            return
        if "push" in event_message:
            queue.put_nowait(event_message)

    async def _process_websocket_messages(self, websocket_assistant: WSAssistant, queue: asyncio.Queue):
        self._ws_assistant = websocket_assistant
        async for ws_response in websocket_assistant.iter_messages():
            data = ws_response.data
            await self._process_event_message(event_message=data, queue=queue)
