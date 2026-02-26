from __future__ import annotations

import frappe
from frappe import _
from frappe.model.document import Document


class SatispaySettings(Document):
	def validate(self) -> None:
		if not self.enable_integration:
			return
		if not self.key_id:
			frappe.throw(_("Key ID is required when Satispay integration is enabled."))
		if not self._get_private_key():
			frappe.throw(_("Private key is required when Satispay integration is enabled."))

	def on_update(self) -> None:
		frappe.clear_cache(doctype="Satispay Settings")

	def _get_private_key(self) -> str:
		value = getattr(self, "private_key", None)
		if value and not self.is_dummy_password(value):
			return value
		try:
			return self.get_password("private_key")
		except frappe.DoesNotExistError:
			return ""
