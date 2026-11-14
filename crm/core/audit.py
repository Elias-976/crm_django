"""Service de traçabilité et journalisation d'audit des modifications du CRM."""

from typing import Any, Dict, Optional
from django.utils import timezone
from .models import AuditAction, AuditLog, Company, Contact, Deal, Task


TRACKED_FIELDS = {
    "Deal": ["title", "amount", "stage", "probability", "expected_close_date", "loss_reason", "owner", "contact", "company"],
    "Contact": ["first_name", "last_name", "email", "phone", "job_title", "lead_status", "lead_score", "owner", "company"],
    "Company": ["name", "domain", "phone", "industry", "owner"],
    "Task": ["title", "due_date", "priority", "status", "assigned_to", "contact", "company", "deal"],
}


def log_audit_event(
    instance,
    action: str,
    user=None,
    changes: Optional[Dict[str, Any]] = None,
    ip_address: Optional[str] = None,
) -> Optional[AuditLog]:
    """Crée une entrée d'audit immuable pour une instance donnée."""
    if not getattr(instance, "organization_id", None):
        return None

    model_name = instance._meta.model_name.capitalize()
    obj_id = str(getattr(instance, "uuid", instance.pk))
    obj_repr = str(instance)[:255]

    kwargs = {
        "organization_id": instance.organization_id,
        "action": action,
        "model_name": model_name,
        "object_id": obj_id,
        "object_repr": obj_repr,
        "user": user,
        "changes": changes or {},
        "ip_address": ip_address,
    }

    if isinstance(instance, Deal):
        kwargs["deal"] = instance
        kwargs["contact"] = instance.contact
        kwargs["company"] = instance.company
    elif isinstance(instance, Contact):
        kwargs["contact"] = instance
        kwargs["company"] = instance.company
    elif isinstance(instance, Company):
        kwargs["company"] = instance
    elif isinstance(instance, Task):
        kwargs["task"] = instance
        kwargs["contact"] = instance.contact
        kwargs["company"] = instance.company
        kwargs["deal"] = instance.deal

    return AuditLog.objects.create(**kwargs)


def capture_model_diff(old_instance, new_instance) -> Dict[str, Dict[str, Any]]:
    """Compare deux états d'une même instance pour extraire les champs modifiés."""
    model_name = new_instance._meta.model_name.capitalize()
    tracked = TRACKED_FIELDS.get(model_name, [])
    diff = {}

    for field in tracked:
        old_val = getattr(old_instance, field, None)
        new_val = getattr(new_instance, field, None)

        if old_val != new_val:
            old_str = str(old_val) if old_val is not None else None
            new_str = str(new_val) if new_val is not None else None
            diff[field] = {
                "old": old_str,
                "new": new_str,
            }

    return diff
