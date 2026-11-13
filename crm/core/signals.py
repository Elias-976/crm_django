"""Signaux pour l'application core."""

from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import Interaction
from .services import sync_entities_last_activity


@receiver(post_save, sender=Interaction, dispatch_uid="interaction_sync_last_activity_post_save")
def handle_interaction_saved(sender, instance, created, raw, **kwargs):
    """
    Signal post_save synchronisant la date de dernière activité sur les entités rattachées.
    - Ignore les fixtures / loaddata bruts (raw=True).
    - Ignore si _skip_activity_sync est explicitement positionné à True (ex: imports de masse).
    """
    if raw or getattr(instance, "_skip_activity_sync", False):
        return

    contact_ids = [instance.contact_id] if instance.contact_id else None
    company_ids = [instance.company_id] if instance.company_id else None
    deal_ids = [instance.deal_id] if instance.deal_id else None

    sync_entities_last_activity(
        contact_ids=contact_ids,
        company_ids=company_ids,
        deal_ids=deal_ids,
        target_time=instance.occurred_at,
    )

