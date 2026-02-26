"""Satispay plugin handler for POS Invoice events.

Invoked by pos_core router, not via doc_events.
"""

from __future__ import annotations

from typing import Any

import frappe
from frappe import _


def handle_pos_invoice_before_submit(doc: Any, context: dict[str, Any]) -> None:
	if getattr(doc, "is_return", 0):
		return

	pos_settings = context.get("settings")
	mop = getattr(pos_settings, "satispay_mode_of_payment", None)
	if not mop:
		frappe.throw(_("Satispay Mode of Payment is not configured in POS Settings."))

	if not _has_payment(doc, mop):
		return

	payment = _find_payment_record(doc)
	if not payment:
		frappe.throw(_("Pagamento Satispay non trovato per questa vendita."))

	status = (payment.get("status") or "").upper()
	if status != "ACCEPTED":
		frappe.throw(_("Pagamento Satispay non completato. Stato attuale: {0}").format(status))


def _has_payment(doc: Any, mop: str) -> bool:
	for row in doc.get("payments", []):
		if row.mode_of_payment == mop and float(row.amount or 0) > 0:
			return True
	return False


def _find_payment_record(doc: Any) -> dict[str, Any] | None:
	if getattr(doc, "name", None):
		record = frappe.db.get_value(
			"Satispay Payment",
			{"pos_invoice": doc.name},
			["name", "status"],
			as_dict=True,
		)
		if record:
			return record

	external_code = getattr(doc, "offline_pos_name", None) or getattr(doc, "name", None)
	if external_code:
		return frappe.db.get_value(
			"Satispay Payment",
			{"external_code": external_code},
			["name", "status"],
			as_dict=True,
		)

	return None
