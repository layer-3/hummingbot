import asyncio
import itertools
import json
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from hummingbot.connector.exchange.yellow_pro import yellow_pro_constants as CONSTANTS, yellow_pro_web_utils as web_utils
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
            trading_pairs: Optional[List[str]],
            connector: "YellowProExchange",
            api_factory: WebAssistantsFactory,
            domain: str = CONSTANTS.DOMAIN):
        super().__init__()
        self._auth = auth
        self._trading_pairs = trading_pairs or []
        self._connector = connector
        self._api_factory = api_factory
        self._domain = domain
        self._request_id_iterator = itertools.count(start=1)
        self._private_channels: List[str] = []

    async def _connected_websocket_assistant(self) -> WSAssistant:
        ws: WSAssistant = await self._api_factory.get_ws_assistant()
        headers: Dict[str, str] = {}
        token = await self._auth.ensure_active_token()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        await ws.connect(
            ws_url=web_utils.wss_url(self._domain),
            ping_timeout=CONSTANTS.HEARTBEAT_TIME_INTERVAL,
            ws_headers=headers,
        )
        return ws

    async def _subscribe_channels(self, websocket_assistant: WSAssistant):
        token = await self._auth.ensure_active_token()
        connect_payload = {
            "id": next(self._request_id_iterator),
            "connect": {"token": token},
        }
        connect_request = WSJSONRequest(payload=connect_payload)
        await websocket_assistant.send(connect_request)

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
            connect_info = event_message["connect"]
            subs = connect_info.get("subs") or {}
            self._private_channels = list(subs.keys())
            return
        if "push" in event_message:
            # Ensure the message is a valid dictionary before queuing
            if isinstance(event_message, dict):
                queue.put_nowait(event_message)
            else:
                # Convert to dictionary if it's a string
                if isinstance(event_message, str):
                    try:
                        parsed_message = json.loads(event_message)
                        if isinstance(parsed_message, dict):
                            queue.put_nowait(parsed_message)
                        else:
                            self.logger().error(f"Unexpected message format after JSON parsing: {type(parsed_message)}")
                    except Exception as e:
                        self.logger().error(f"Failed to parse string message as JSON: {e}")
                        self.logger().error(f"Message content: {event_message[:200]}...")
                else:
                    self.logger().error(f"Unexpected message type in _process_event_message: {type(event_message)}")

    async def _process_websocket_messages(self, websocket_assistant: WSAssistant, queue: asyncio.Queue):
        async for ws_response in websocket_assistant.iter_messages():
            data = ws_response.data
            await self._process_event_message(event_message=data, queue=queue)
