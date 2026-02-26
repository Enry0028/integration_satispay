from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlencode
from uuid import uuid4

import frappe
from frappe import _
from frappe.utils import flt, now_datetime

from integration_satispay.integration_satispay.satispay_client import SatispayClient
from integration_satispay.integration_satispay.settings import (
	as_service_user,
	get_base_url,
	get_callback_token,
	get_private_key,
	get_settings,
)

SUPPORTED_CHANNELS = {
	"kiosk": "/kiosk",
	"webapp": "/webapp",
	"pos": "/app/pos",
}


@frappe.whitelist(allow_guest=True)
def create_payment(payload: dict[str, Any] | str | None = None, channel: str | None = None) -> dict[str, Any]:
	data = _coerce_payload(payload)
	with as_service_user():
		pos_settings = _get_pos_settings()
		if not pos_settings.enable_satispay:
			frappe.throw(_("Satispay is not enabled in POS Settings."))

		settings = get_settings()
		if not settings.enable_integration:
			frappe.throw(_("Enable Satispay in Satispay Settings before creating payments."))
		if not settings.key_id:
			frappe.throw(_("Satispay Key ID is missing in Satispay Settings."))
		if not get_private_key(settings):
			frappe.throw(_("Satispay private key is missing in Satispay Settings."))

		items = data.get("items") or []
		if not items:
			frappe.throw(_("At least one item is required"), title=_("Missing Items"))

		amount = _calculate_total(items)
		if amount <= 0:
			frappe.throw(_("Total amount must be greater than zero."))

		mop = pos_settings.satispay_mode_of_payment
		if not mop:
			frappe.throw(_("Satispay Mode of Payment is not configured in POS Settings."))

		external_code = data.get("external_ref") or f"sp-{uuid4().hex}"
		data["external_ref"] = external_code
		_apply_defaults(data, pos_settings)
		data["payments"] = [{"mode_of_payment": mop, "amount": amount}]

		currency = data.get("currency") or pos_settings.default_currency or "EUR"
		amount_unit = int(round(amount * 100))

		callback_url = _build_callback_url(external_code)
		return_url = _build_return_url(channel, external_code)

		client = _build_client(settings)
		response = client.request(
			"POST",
			"/payments",
			{
				"flow": "MATCH_CODE",
				"amount_unit": amount_unit,
				"currency": currency,
				"external_code": external_code,
				"callback_url": callback_url,
				"redirect_url": return_url,
				"metadata": {
					"external_code": external_code,
					"channel": channel or "pos",
				},
			},
		)

		payment_doc = frappe.new_doc("Satispay Payment")
		payment_doc.payment_id = response.get("id")
		payment_doc.external_code = external_code
		payment_doc.status = response.get("status") or "PENDING"
		payment_doc.amount_unit = response.get("amount_unit") or amount_unit
		payment_doc.currency = response.get("currency") or currency
		payment_doc.flow = response.get("flow") or "MATCH_CODE"
		payment_doc.redirect_url = response.get("redirect_url") or ""
		payment_doc.callback_url = callback_url
		payment_doc.channel = channel or "pos"
		payment_doc.payload = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
		payment_doc.last_synced_at = now_datetime()
		payment_doc.insert(ignore_permissions=True)

		return {
			"payment_id": payment_doc.payment_id,
			"redirect_url": payment_doc.redirect_url,
			"external_code": external_code,
			"status": payment_doc.status,
		}


@frappe.whitelist(allow_guest=True)
def sync_payment(
	payment_id: str | None = None,
	external_code: str | None = None,
) -> dict[str, Any]:
	with as_service_user():
		payment_doc = _get_payment_doc(payment_id=payment_id, external_code=external_code)
		settings = get_settings()
		client = _build_client(settings)

		response = client.request("GET", f"/payments/{payment_doc.payment_id}")
		status = response.get("status") or payment_doc.status

		payment_doc.status = status
		payment_doc.redirect_url = response.get("redirect_url") or payment_doc.redirect_url
		payment_doc.last_synced_at = now_datetime()
		payment_doc.save(ignore_permissions=True)

		invoice_name = payment_doc.pos_invoice
		order_number = None
		if status == "ACCEPTED":
			invoice_name, order_number = _ensure_invoice(payment_doc)

		return {
			"status": status,
			"pos_invoice": invoice_name,
			"barciware_order_number": order_number,
			"external_code": payment_doc.external_code,
		}


@frappe.whitelist(allow_guest=True)
def satispay_callback(
	payment_id: str | None = None,
	external_code: str | None = None,
	token: str | None = None,
) -> dict[str, Any]:
	try:
		with as_service_user():
			settings = get_settings()
			callback_token = get_callback_token(settings)
			if callback_token and token != callback_token:
				frappe.throw(_("Invalid callback token"))
			return sync_payment(payment_id=payment_id, external_code=external_code)
	except Exception:
		frappe.log_error(title="Satispay callback failed", message=frappe.get_traceback())
		return {"status": "error"}


def _build_client(settings) -> SatispayClient:
	return SatispayClient(
		base_url=get_base_url(settings),
		key_id=settings.key_id,
		private_key_pem=get_private_key(settings),
		timeout=settings.request_timeout or 10,
		log_requests=bool(settings.log_requests),
	)


def _coerce_payload(payload: dict[str, Any] | str | None) -> dict[str, Any]:
	if not payload:
		return {}
	if isinstance(payload, dict):
		return payload
	try:
		return json.loads(payload)
	except ValueError as err:
		raise frappe.ValidationError(_("Payload must be valid JSON")) from err


def _apply_defaults(data: dict[str, Any], pos_settings) -> None:
	data.setdefault("pos_profile", pos_settings.default_pos_profile)
	data.setdefault("company", pos_settings.default_company)
	data.setdefault("price_list", pos_settings.default_price_list)
	data.setdefault("warehouse", pos_settings.default_warehouse)
	data.setdefault("customer", pos_settings.default_customer)
	data.setdefault("currency", pos_settings.default_currency)


def _calculate_total(items: list[dict[str, Any]]) -> float:
	total = 0.0
	for row in items:
		qty = flt(row.get("qty") or 0)
		rate = flt(row.get("rate") or 0)
		total += qty * rate
	return float(total)


def _build_callback_url(external_code: str) -> str:
	base_url = frappe.utils.get_url()
	params = {"external_code": external_code}
	settings = get_settings()
	token = get_callback_token(settings)
	if token:
		params["token"] = token
	return f"{base_url}/api/method/integration_satispay.integration_satispay.api.payment.satispay_callback?{urlencode(params)}"


def _build_return_url(channel: str | None, external_code: str) -> str:
	path = SUPPORTED_CHANNELS.get((channel or "").lower(), "/")
	base_url = frappe.utils.get_url()
	return f"{base_url}{path}?{urlencode({'satispay_ref': external_code, 'channel': channel or 'pos'})}"


def _get_pos_settings():
	try:
		return frappe.get_cached_doc("POS Settings")
	except frappe.DoesNotExistError as exc:
		raise frappe.ValidationError(_("POS Settings not found.")) from exc


def _get_payment_doc(payment_id: str | None, external_code: str | None):
	if not payment_id and not external_code:
		frappe.throw(_("payment_id or external_code is required."))

	filters = {}
	if payment_id:
		filters["payment_id"] = payment_id
	if external_code:
		filters["external_code"] = external_code

	name = frappe.db.get_value("Satispay Payment", filters, "name")
	if not name:
		frappe.throw(_("Satispay payment not found."))
	return frappe.get_doc("Satispay Payment", name)


def _ensure_invoice(payment_doc) -> tuple[str | None, str | None]:
	if payment_doc.pos_invoice:
		invoice = frappe.get_doc("POS Invoice", payment_doc.pos_invoice)
		return invoice.name, invoice.get("barciware_order_number")

	if not payment_doc.payload:
		frappe.throw(_("Missing order payload for Satispay payment."))

	payload = json.loads(payment_doc.payload)
	with as_service_user():
		result = frappe.call("pos_core.api.pos.create_pos_invoice", payload=payload)

	invoice_name = result.get("name")
	if invoice_name:
		payment_doc.pos_invoice = invoice_name
		payment_doc.save(ignore_permissions=True)
		order_number = result.get("barciware_order_number")
		return invoice_name, order_number

	return None, None
