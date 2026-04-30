from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, Iterator, List, MutableMapping, Optional

import httpx

from .models import StreamEvent
from .stream import iter_sse_events


class MmdClientError(RuntimeError):
    pass


def _join_api_path(path: str) -> str:
    normalized = str(path or "").strip()
    if not normalized:
        return "/api"
    if normalized.startswith("/api/"):
        return normalized
    if normalized == "/api":
        return normalized
    if not normalized.startswith("/"):
        normalized = f"/{normalized}"
    return f"/api{normalized}"


def _parse_json_response(response: httpx.Response) -> Any:
    content_type = response.headers.get("content-type", "")
    if "application/json" not in content_type:
        raise MmdClientError(f"Expected JSON response, got {content_type or 'empty body'}")
    return response.json()


def _extract_error_message(data: Any, status_code: int) -> str:
    if isinstance(data, dict):
        error = data.get("error")
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            return error["message"]
        detail = data.get("detail")
        if isinstance(detail, str) and detail.strip():
            return detail.strip()
    return f"Request failed with status {status_code}"


@dataclass
class MmdClient:
    base_url: str = "http://127.0.0.1:8000"
    timeout: float = 30.0
    trust_env: bool = False

    def __post_init__(self) -> None:
        self.base_url = self.base_url.rstrip("/")
        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=self.timeout,
            trust_env=self.trust_env,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "MmdClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def request_json(
        self,
        path: str,
        *,
        method: str = "GET",
        json_body: Optional[MutableMapping[str, Any]] = None,
    ) -> Any:
        response = self._client.request(
            method=method,
            url=_join_api_path(path),
            json=json_body,
        )
        if response.status_code >= 400:
            data = None
            try:
                data = response.json()
            except Exception:
                data = None
            raise MmdClientError(_extract_error_message(data, response.status_code))
        return _parse_json_response(response)

    def list_sessions(self) -> List[Dict[str, Any]]:
        data = self.request_json("/sessions")
        return data if isinstance(data, list) else []

    def get_session(self, session_id: str) -> Dict[str, Any]:
        data = self.request_json(f"/sessions/{session_id}")
        if not isinstance(data, dict):
            raise MmdClientError("Session payload was not an object")
        return data

    def update_session_config(
        self,
        session_id: str,
        payload: MutableMapping[str, Any],
    ) -> Dict[str, Any]:
        data = self.request_json(
            f"/sessions/{session_id}/config",
            method="PATCH",
            json_body=dict(payload),
        )
        if not isinstance(data, dict):
            raise MmdClientError("Session config update response was not an object")
        return data

    def create_session(self, payload: MutableMapping[str, Any]) -> Dict[str, Any]:
        data = self.request_json("/sessions", method="POST", json_body=dict(payload))
        if not isinstance(data, dict):
            raise MmdClientError("Session creation response was not an object")
        return data

    def list_providers(self) -> List[Dict[str, Any]]:
        data = self.request_json("/providers")
        return data if isinstance(data, list) else []

    def discover_models(
        self,
        *,
        provider_id: Optional[str] = None,
        provider: Optional[MutableMapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {}
        if provider_id:
            payload["provider_id"] = provider_id
        elif provider is not None:
            payload["provider"] = dict(provider)
        else:
            raise MmdClientError("provider_id or provider is required")
        data = self.request_json("/model-catalog/discover", method="POST", json_body=payload)
        if not isinstance(data, dict):
            raise MmdClientError("Model discovery response was not an object")
        return data

    def append_session_participant(
        self,
        session_id: str,
        payload: MutableMapping[str, Any],
    ) -> Dict[str, Any]:
        data = self.request_json(
            f"/sessions/{session_id}/participants",
            method="POST",
            json_body=dict(payload),
        )
        if not isinstance(data, dict):
            raise MmdClientError("Participant append response was not an object")
        return data

    def append_session_participants(
        self,
        session_id: str,
        participants: Iterable[MutableMapping[str, Any]],
    ) -> Dict[str, Any]:
        data = self.request_json(
            f"/sessions/{session_id}/participants/batch",
            method="POST",
            json_body={"participants": [dict(item) for item in participants]},
        )
        if not isinstance(data, dict):
            raise MmdClientError("Participant batch response was not an object")
        return data

    def rename_session_participant(
        self,
        session_id: str,
        custom_id: str,
        new_custom_id: str,
    ) -> Dict[str, Any]:
        data = self.request_json(
            f"/sessions/{session_id}/participants/{custom_id}",
            method="PATCH",
            json_body={"new_custom_id": new_custom_id},
        )
        if not isinstance(data, dict):
            raise MmdClientError("Participant rename response was not an object")
        return data

    def remove_session_participant(self, session_id: str, custom_id: str) -> Dict[str, Any]:
        data = self.request_json(
            f"/sessions/{session_id}/participants/{custom_id}",
            method="DELETE",
        )
        if not isinstance(data, dict):
            raise MmdClientError("Participant remove response was not an object")
        return data

    def clone_session(
        self,
        session_id: str,
        *,
        topic: Optional[str] = None,
        mode: Optional[str] = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {}
        if topic is not None:
            payload["topic"] = topic
        if mode is not None:
            payload["mode"] = mode
        data = self.request_json(
            f"/sessions/{session_id}/clone",
            method="POST",
            json_body=payload,
        )
        if not isinstance(data, dict):
            raise MmdClientError("Session clone response was not an object")
        return data

    def send_user_message(self, session_id: str, content: str) -> Dict[str, Any]:
        data = self.request_json(
            f"/sessions/{session_id}/messages",
            method="POST",
            json_body={"content": content},
        )
        if not isinstance(data, dict):
            raise MmdClientError("Message response was not an object")
        return data

    def get_snapshot(self, session_id: str) -> Dict[str, Any]:
        data = self.request_json(f"/sessions/{session_id}/snapshot")
        if not isinstance(data, dict):
            raise MmdClientError("Snapshot response was not an object")
        return data

    def get_workspace(self, session_id: str) -> Dict[str, Any]:
        data = self.request_json(f"/sessions/{session_id}/workspace")
        if not isinstance(data, dict):
            raise MmdClientError("Workspace response was not an object")
        return data

    def stream_session(self, session_id: str) -> Iterator[StreamEvent]:
        with self._client.stream("GET", _join_api_path(f"/sessions/{session_id}/stream")) as response:
            if response.status_code >= 400:
                data = None
                try:
                    data = response.json()
                except Exception:
                    data = None
                raise MmdClientError(_extract_error_message(data, response.status_code))
            yield from iter_sse_events(response.iter_lines())
