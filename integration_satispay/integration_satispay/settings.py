from __future__ import annotations

from contextlib import contextmanager

import frappe
from frappe import _

PRODUCTION_URL = "https://authservices.satispay.com/g_business/v1"
SANDBOX_URL = "https://staging.authservices.satispay.com/g_business/v1"

SERVICE_CONF_KEY = "satispay_service_user"


def get_settings(cache: bool = True):
	try:
		return frappe.get_cached_doc("Satispay Settings") if cache else frappe.get_doc("Satispay Settings")
	except frappe.DoesNotExistError as exc:
		raise frappe.ValidationError(_("Satispay Settings not found.")) from exc


def get_base_url(settings) -> str:
	environment = (settings.environment or "Sandbox").strip().lower()
	if environment == "production":
		return PRODUCTION_URL
	return SANDBOX_URL


def get_private_key(settings) -> str:
	value = getattr(settings, "private_key", None)
	if value and not settings.is_dummy_password(value):
		return value
	try:
		return settings.get_password("private_key")
	except frappe.DoesNotExistError:
		return ""


def get_callback_token(settings) -> str:
	value = getattr(settings, "callback_token", None)
	if value and not settings.is_dummy_password(value):
		return value
	try:
		return settings.get_password("callback_token")
	except frappe.DoesNotExistError:
		return ""


def get_service_user() -> str:
	conf_user = frappe.conf.get(SERVICE_CONF_KEY)
	if conf_user:
		return conf_user
	webapp_user = frappe.conf.get("pos_webapp_service_user")
	if webapp_user:
		return webapp_user
	return "Administrator"


@contextmanager
def as_service_user():
	original = frappe.session.user
	target = get_service_user()
	if target and target != original:
		frappe.set_user(target)
	try:
		yield
	finally:
		if target and target != original:
			frappe.set_user(original)
