# """
# HTTP client wrapping all LexAra backend API calls.
# """
# import json
# import os
# import time
# from collections.abc import Generator
# from typing import Any

# import requests

# _BASE_URL = os.environ.get("LEXARA_API_URL", "http://localhost:8000").rstrip("/")
# _API_PREFIX = "/api/v1"
# _TIMEOUT = 30
# _STREAM_TIMEOUT = 120
# _MAX_RETRIES = 3
# _RETRY_CODES = {503, 502, 504}


# class LexaraAPIError(Exception):
#     def __init__(self, status_code: int, detail: str) -> None:
#         self.status_code = status_code
#         self.detail = detail
#         super().__init__(f"API error {status_code}: {detail}")


# def _url(path: str) -> str:
#     return f"{_BASE_URL}{_API_PREFIX}{path}"


# def _request(
#     method: str,
#     path: str,
#     *,
#     json_body: dict | None = None,
#     files: dict | None = None,
#     params: dict | None = None,
#     timeout: int = _TIMEOUT,
# ) -> dict | list:
#     """Make an HTTP request with retry logic on 5xx errors."""
#     url = _url(path)
#     last_exc: Exception | None = None

#     for attempt in range(_MAX_RETRIES):
#         try:
#             resp = requests.request(
#                 method,
#                 url,
#                 json=json_body,
#                 files=files,
#                 params=params,
#                 timeout=timeout,
#             )
#             if resp.status_code in _RETRY_CODES and attempt < _MAX_RETRIES - 1:
#                 time.sleep(2 ** attempt)
#                 continue
#             if not resp.ok:
#                 try:
#                     detail = resp.json().get("detail", resp.text)
#                 except Exception:
#                     detail = resp.text
#                 raise LexaraAPIError(resp.status_code, detail)
#             if resp.status_code == 204:
#                 return {}
#             return resp.json()
#         except LexaraAPIError:
#             raise
#         except requests.exceptions.ConnectionError as exc:
#             last_exc = exc
#             if attempt < _MAX_RETRIES - 1:
#                 time.sleep(2 ** attempt)
#             continue
#         except Exception as exc:
#             raise LexaraAPIError(0, str(exc)) from exc

#     raise LexaraAPIError(0, f"Connection failed after {_MAX_RETRIES} retries: {last_exc}")


# class LexaraAPIClient:
#     """Synchronous HTTP client for the LexAra FastAPI backend."""

#     # ------------------------------------------------------------------
#     # Sessions
#     # ------------------------------------------------------------------

#     def create_session(
#         self,
#         jurisdiction: str | None = None,
#         legal_domain: str | None = None,
#     ) -> dict:
#         """Create a new chat session. Returns {id, created_at, jurisdiction, ...}"""
#         return _request(
#             "POST",
#             "/chat/sessions",
#             json_body={
#                 "jurisdiction": jurisdiction,
#                 "legal_domain": legal_domain,
#                 "session_metadata": {},
#             },
#         )

#     def get_history(self, session_id: str) -> dict:
#         """Return full message history for a session."""
#         return _request("GET", f"/chat/sessions/{session_id}/history")

#     def delete_session(self, session_id: str) -> None:
#         _request("DELETE", f"/chat/sessions/{session_id}")

#     # ------------------------------------------------------------------
#     # Messaging (SSE streaming)
#     # ------------------------------------------------------------------

#     def send_message_stream(
#         self,
#         session_id: str,
#         content: str,
#         document_ids: list[str] | None = None,
#     ) -> Generator[dict[str, Any], None, None]:
#         """
#         POST a message and stream SSE events back.
#         Yields parsed event dicts: {type: str, content: Any}
#         """
#         url = _url(f"/chat/sessions/{session_id}/messages")
#         payload = {"content": content, "document_ids": document_ids or []}

#         try:
#             with requests.post(
#                 url,
#                 json=payload,
#                 stream=True,
#                 timeout=_STREAM_TIMEOUT,
#             ) as resp:
#                 if not resp.ok:
#                     try:
#                         detail = resp.json().get("detail", resp.text)
#                     except Exception:
#                         detail = resp.text
#                     raise LexaraAPIError(resp.status_code, detail)

#                 for raw_line in resp.iter_lines(decode_unicode=True):
#                     if not raw_line:
#                         continue
#                     if raw_line.startswith("data: "):
#                         data_str = raw_line[6:]
#                         try:
#                             yield json.loads(data_str)
#                         except json.JSONDecodeError:
#                             continue
#         except LexaraAPIError:
#             raise
#         except Exception as exc:
#             raise LexaraAPIError(0, f"Stream error: {exc}") from exc

#     # ------------------------------------------------------------------
#     # Documents
#     # ------------------------------------------------------------------

#     def upload_document(
#         self,
#         file_bytes: bytes,
#         filename: str,
#         mime_type: str,
#         session_id: str | None = None,
#     ) -> dict:
#         """Upload a document for OCR and analysis. Returns {document_id, status, polling_url}."""
#         params = {}
#         if session_id:
#             params["session_id"] = session_id
#         return _request(
#             "POST",
#             "/documents/upload",
#             files={"file": (filename, file_bytes, mime_type)},
#             params=params or None,
#         )

#     def poll_document_analysis(self, document_id: str) -> dict:
#         """Poll for document analysis result. Check result['ocr_status'] for 'completed'."""
#         return _request("GET", f"/documents/{document_id}/analysis")

#     def delete_document(self, document_id: str) -> None:
#         _request("DELETE", f"/documents/{document_id}")

#     # ------------------------------------------------------------------
#     # Cases
#     # ------------------------------------------------------------------

#     def submit_case(self, case_data: dict) -> dict:
#         """Submit a case for anonymization and ingestion."""
#         return _request("POST", "/cases", json_body=case_data)

#     def get_similar_cases(
#         self,
#         situation: str,
#         jurisdiction: str,
#         legal_domain: str | None = None,
#         top_k: int = 3,
#     ) -> list[dict]:
#         """Find similar past cases given a situation description."""
#         result = _request(
#             "POST",
#             "/cases/similar",
#             json_body={
#                 "situation": situation,
#                 "jurisdiction": jurisdiction,
#                 "legal_domain": legal_domain,
#                 "top_k": top_k,
#             },
#         )
#         return result if isinstance(result, list) else []

#     def get_case(self, case_id: str) -> dict:
#         return _request("GET", f"/cases/{case_id}")

#     # ------------------------------------------------------------------
#     # Feedback
#     # ------------------------------------------------------------------

#     def submit_feedback(
#         self,
#         message_id: str,
#         rating: int,
#         feedback_type: str | None = None,
#         session_id: str | None = None,
#         contributed_case_ids: list[str] | None = None,
#         contributed_source_ids: list[str] | None = None,
#     ) -> dict:
#         """Submit 1-5 star rating for an assistant message."""
#         return _request(
#             "POST",
#             "/feedback",
#             json_body={
#                 "message_id": message_id,
#                 "session_id": session_id,
#                 "rating": rating,
#                 "feedback_type": feedback_type,
#                 "contributed_case_ids": contributed_case_ids or [],
#                 "contributed_source_ids": contributed_source_ids or [],
#             },
#         )

#     # ------------------------------------------------------------------
#     # Workflows
#     # ------------------------------------------------------------------

#     def list_workflows(
#         self,
#         jurisdiction: str | None = None,
#         legal_domain: str | None = None,
#     ) -> list[dict]:
#         params: dict[str, str] = {}
#         if jurisdiction:
#             params["jurisdiction"] = jurisdiction
#         if legal_domain:
#             params["legal_domain"] = legal_domain
#         result = _request("GET", "/workflows", params=params or None)
#         return result if isinstance(result, list) else []

#     def get_workflow(self, procedure_id: str) -> dict:
#         return _request("GET", f"/workflows/{procedure_id}")

#     # ------------------------------------------------------------------
#     # Health
#     # ------------------------------------------------------------------

#     @staticmethod
#     def health_check() -> dict:
#         try:
#             resp = requests.get(_url("/health"), timeout=5)
#             return resp.json()
#         except Exception as exc:
#             return {"status": "error", "detail": str(exc)}


# # Module-level singleton
# _client: LexaraAPIClient | None = None


# def get_client() -> LexaraAPIClient:
#     global _client
#     if _client is None:
#         _client = LexaraAPIClient()
#     return _client


"""
HTTP client wrapping all LexAra backend API calls.
"""

import json
import os
import time
from collections.abc import Generator
from typing import Any, cast

import requests

_BASE_URL = os.environ.get("LEXARA_API_URL", "http://localhost:8000").rstrip("/")
_API_PREFIX = "/api/v1"

_TIMEOUT = 30
_STREAM_TIMEOUT = 120

_MAX_RETRIES = 3
_RETRY_CODES = {502, 503, 504}


class LexaraAPIError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail

        super().__init__(f"API error {status_code}: {detail}")


def _url(path: str) -> str:
    return f"{_BASE_URL}{_API_PREFIX}{path}"


def _request(
    method: str,
    path: str,
    *,
    json_body: dict[str, Any] | None = None,
    files: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    timeout: int = _TIMEOUT,
) -> dict[str, Any] | list[dict[str, Any]]:
    """
    Make an HTTP request with retry logic on transient 5xx errors.
    """

    url = _url(path)

    last_exc: Exception | None = None

    for attempt in range(_MAX_RETRIES):
        try:
            resp = requests.request(
                method,
                url,
                json=json_body,
                files=files,
                params=params,
                timeout=timeout,
            )

            # Retry transient upstream/server errors
            if (
                resp.status_code in _RETRY_CODES
                and attempt < _MAX_RETRIES - 1
            ):
                time.sleep(2**attempt)
                continue

            if not resp.ok:
                try:
                    payload = resp.json()

                    detail = (
                        payload.get("detail", resp.text)
                        if isinstance(payload, dict)
                        else resp.text
                    )

                except Exception:
                    detail = resp.text

                raise LexaraAPIError(resp.status_code, str(detail))

            # No-content responses
            if resp.status_code == 204:
                return {}

            data = resp.json()

            if isinstance(data, dict):
                return cast(dict[str, Any], data)

            if isinstance(data, list):
                return cast(list[dict[str, Any]], data)

            raise LexaraAPIError(
                0,
                f"Unexpected response type: {type(data).__name__}",
            )

        except LexaraAPIError:
            raise

        except requests.exceptions.ConnectionError as exc:
            last_exc = exc

            if attempt < _MAX_RETRIES - 1:
                time.sleep(2**attempt)

            continue

        except Exception as exc:
            raise LexaraAPIError(0, str(exc)) from exc

    raise LexaraAPIError(
        0,
        f"Connection failed after {_MAX_RETRIES} retries: {last_exc}",
    )


class LexaraAPIClient:
    """
    Synchronous HTTP client for the LexAra FastAPI backend.
    """

    # ------------------------------------------------------------------
    # Sessions
    # ------------------------------------------------------------------

    def create_session(
        self,
        jurisdiction: str | None = None,
        legal_domain: str | None = None,
    ) -> dict[str, Any]:
        """
        Create a new chat session.
        """

        result = _request(
            "POST",
            "/chat/sessions",
            json_body={
                "jurisdiction": jurisdiction,
                "legal_domain": legal_domain,
                "session_metadata": {},
            },
        )

        if not isinstance(result, dict):
            raise LexaraAPIError(0, "Expected dict response")

        return result

    def get_history(self, session_id: str) -> dict[str, Any]:
        """
        Return full message history for a session.
        """

        result = _request(
            "GET",
            f"/chat/sessions/{session_id}/history",
        )

        if not isinstance(result, dict):
            raise LexaraAPIError(0, "Expected dict response")

        return result

    def delete_session(self, session_id: str) -> None:
        _request("DELETE", f"/chat/sessions/{session_id}")

    # ------------------------------------------------------------------
    # Messaging (SSE streaming)
    # ------------------------------------------------------------------

    def send_message_stream(
        self,
        session_id: str,
        content: str,
        document_ids: list[str] | None = None,
    ) -> Generator[dict[str, Any], None, None]:
        """
        POST a message and stream SSE events back.

        Yields:
            {
                "type": str,
                "content": Any,
            }
        """

        url = _url(f"/chat/sessions/{session_id}/messages")

        payload = {
            "content": content,
            "document_ids": document_ids or [],
        }

        try:
            with requests.post(
                url,
                json=payload,
                stream=True,
                timeout=_STREAM_TIMEOUT,
            ) as resp:

                if not resp.ok:
                    try:
                        payload = resp.json()

                        detail = (
                            payload.get("detail", resp.text)
                            if isinstance(payload, dict)
                            else resp.text
                        )

                    except Exception:
                        detail = resp.text

                    raise LexaraAPIError(resp.status_code, str(detail))

                for raw_line in resp.iter_lines(decode_unicode=True):

                    if raw_line is None:
                        continue

                    # Normalize to string for type safety
                    if not isinstance(raw_line, str):
                        raw_line = bytes(raw_line).decode("utf-8")

                    if not raw_line:
                        continue

                    if raw_line.startswith("data: "):
                        data_str = raw_line[6:]

                        try:
                            parsed = json.loads(data_str)

                            if isinstance(parsed, dict):
                                yield cast(dict[str, Any], parsed)

                        except json.JSONDecodeError:
                            continue

        except LexaraAPIError:
            raise

        except Exception as exc:
            raise LexaraAPIError(
                0,
                f"Stream error: {exc}",
            ) from exc

    # ------------------------------------------------------------------
    # Documents
    # ------------------------------------------------------------------

    def upload_document(
        self,
        file_bytes: bytes,
        filename: str,
        mime_type: str,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Upload a document for OCR and analysis.
        """

        params: dict[str, str] = {}

        if session_id:
            params["session_id"] = session_id

        result = _request(
            "POST",
            "/documents/upload",
            files={
                "file": (
                    filename,
                    file_bytes,
                    mime_type,
                )
            },
            params=params or None,
        )

        if not isinstance(result, dict):
            raise LexaraAPIError(0, "Expected dict response")

        return result

    def poll_document_analysis(
        self,
        document_id: str,
    ) -> dict[str, Any]:
        """
        Poll for document analysis result.
        """

        result = _request(
            "GET",
            f"/documents/{document_id}/analysis",
        )

        if not isinstance(result, dict):
            raise LexaraAPIError(0, "Expected dict response")

        return result

    def delete_document(self, document_id: str) -> None:
        _request("DELETE", f"/documents/{document_id}")

    # ------------------------------------------------------------------
    # Cases
    # ------------------------------------------------------------------

    def submit_case(
        self,
        case_data: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Submit a case for anonymization and ingestion.
        """

        result = _request(
            "POST",
            "/cases",
            json_body=case_data,
        )

        if not isinstance(result, dict):
            raise LexaraAPIError(0, "Expected dict response")

        return result

    def get_similar_cases(
        self,
        situation: str,
        jurisdiction: str,
        legal_domain: str | None = None,
        top_k: int = 3,
    ) -> list[dict[str, Any]]:
        """
        Find similar past cases given a situation description.
        """

        result = _request(
            "POST",
            "/cases/similar",
            json_body={
                "situation": situation,
                "jurisdiction": jurisdiction,
                "legal_domain": legal_domain,
                "top_k": top_k,
            },
        )

        return result if isinstance(result, list) else []

    def get_case(self, case_id: str) -> dict[str, Any]:
        result = _request(
            "GET",
            f"/cases/{case_id}",
        )

        if not isinstance(result, dict):
            raise LexaraAPIError(0, "Expected dict response")

        return result

    # ------------------------------------------------------------------
    # Feedback
    # ------------------------------------------------------------------

    def submit_feedback(
        self,
        message_id: str,
        rating: int,
        feedback_type: str | None = None,
        session_id: str | None = None,
        contributed_case_ids: list[str] | None = None,
        contributed_source_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """
        Submit 1-5 star feedback for an assistant message.
        """

        result = _request(
            "POST",
            "/feedback",
            json_body={
                "message_id": message_id,
                "session_id": session_id,
                "rating": rating,
                "feedback_type": feedback_type,
                "contributed_case_ids": contributed_case_ids or [],
                "contributed_source_ids": contributed_source_ids or [],
            },
        )

        if not isinstance(result, dict):
            raise LexaraAPIError(0, "Expected dict response")

        return result

    # ------------------------------------------------------------------
    # Workflows
    # ------------------------------------------------------------------

    def list_workflows(
        self,
        jurisdiction: str | None = None,
        legal_domain: str | None = None,
    ) -> list[dict[str, Any]]:

        params: dict[str, str] = {}

        if jurisdiction:
            params["jurisdiction"] = jurisdiction

        if legal_domain:
            params["legal_domain"] = legal_domain

        result = _request(
            "GET",
            "/workflows",
            params=params or None,
        )

        return result if isinstance(result, list) else []

    def get_workflow(
        self,
        procedure_id: str,
    ) -> dict[str, Any]:

        result = _request(
            "GET",
            f"/workflows/{procedure_id}",
        )

        if not isinstance(result, dict):
            raise LexaraAPIError(0, "Expected dict response")

        return result

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    @staticmethod
    def health_check() -> dict[str, Any]:
        try:
            resp = requests.get(
                _url("/health"),
                timeout=5,
            )

            data = resp.json()

            if isinstance(data, dict):
                return cast(dict[str, Any], data)

            return {
                "status": "error",
                "detail": "Unexpected health response format",
            }

        except Exception as exc:
            return {
                "status": "error",
                "detail": str(exc),
            }


# ----------------------------------------------------------------------
# Module-level singleton
# ----------------------------------------------------------------------

_client: LexaraAPIClient | None = None


def get_client() -> LexaraAPIClient:
    global _client

    if _client is None:
        _client = LexaraAPIClient()

    return _client