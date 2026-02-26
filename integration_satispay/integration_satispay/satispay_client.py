from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding


class SatispayError(RuntimeError):
	pass


class SatispayClient:
	def __init__(
		self,
		base_url: str,
		key_id: str,
		private_key_pem: str,
		timeout: int = 10,
		log_requests: bool = False,
	) -> None:
		self.base_url = base_url.rstrip("/")
		self.key_id = key_id
		self.timeout = max(int(timeout or 0), 1)
		self.log_requests = bool(log_requests)
		self._private_key = self._load_private_key(private_key_pem)

	def request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
		method = method.upper()
		body = json.dumps(payload or {}, separators=(",", ":"), ensure_ascii=False) if payload else ""
		full_url = self._build_url(path)
		headers = self._build_headers(method, full_url, body)

		try:
			response = requests.request(
				method,
				full_url,
				data=body if body else None,
				headers=headers,
				timeout=self.timeout,
			)
		except requests.RequestException as exc:
			raise SatispayError(f"HTTP request failed: {exc}") from exc

		return self._parse_response(response)

	def _build_url(self, path: str) -> str:
		if path.startswith("/"):
			path = path[1:]
		return f"{self.base_url}/{path}"

	def _build_headers(self, method: str, url: str, body: str) -> dict[str, str]:
		parsed = urlparse(url)
		request_path = parsed.path
		if parsed.query:
			request_path = f"{request_path}?{parsed.query}"
		host = parsed.netloc
		date = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")
		digest = self._build_digest(body)
		message = "\n".join(
			[
				f"(request-target): {method.lower()} {request_path}",
				f"host: {host}",
				f"date: {date}",
				f"digest: {digest}",
			]
		)
		signature = self._sign_message(message)
		authorization = (
			'Signature keyId="{key_id}", algorithm="rsa-sha256", '
			'headers="(request-target) host date digest", signature="{sig}"'
		).format(key_id=self.key_id, sig=signature)

		return {
			"Accept": "application/json",
			"Content-Type": "application/json",
			"Host": host,
			"Date": date,
			"Digest": digest,
			"Authorization": authorization,
		}

	@staticmethod
	def _build_digest(body: str) -> str:
		digest = hashlib.sha256(body.encode("utf-8")).digest()
		encoded = base64.b64encode(digest).decode("utf-8")
		return f"SHA-256={encoded}"

	def _sign_message(self, message: str) -> str:
		signature = self._private_key.sign(
			message.encode("utf-8"),
			padding.PKCS1v15(),
			hashes.SHA256(),
		)
		return base64.b64encode(signature).decode("utf-8")

	@staticmethod
	def _load_private_key(private_key_pem: str):
		if not private_key_pem:
			raise SatispayError("Private key is missing")
		try:
			return serialization.load_pem_private_key(
				private_key_pem.encode("utf-8"),
				password=None,
			)
		except Exception as exc:
			raise SatispayError("Invalid private key") from exc

	def _parse_response(self, response: requests.Response) -> dict[str, Any]:
		payload = None
		text = response.text or ""
		if response.headers.get("content-type", "").startswith("application/json"):
			try:
				payload = response.json()
			except ValueError:
				payload = None

		if response.status_code >= 400:
			error_msg = self._extract_error_message(payload, text)
			raise SatispayError(f"Satispay API error ({response.status_code}): {error_msg}")

		if payload is None:
			raise SatispayError("Unexpected empty response from Satispay")

		return payload

	@staticmethod
	def _extract_error_message(payload: dict[str, Any] | None, text: str) -> str:
		if isinstance(payload, dict):
			message = payload.get("message") or payload.get("error")
			if message:
				return str(message)
		return text.strip() or "Unknown error"
