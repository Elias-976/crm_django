"""Services et logiques mutualisées pour le socle CRM."""

from datetime import datetime
from typing import Iterable, Optional
from django.db.models import Q
from django.utils import timezone


def sync_entities_last_activity(
    contact_ids: Optional[Iterable[int]] = None,
    company_ids: Optional[Iterable[int]] = None,
    deal_ids: Optional[Iterable[int]] = None,
    target_time: Optional[datetime] = None,
) -> None:
    """
    Met à jour efficacement 'last_activity_at' sur les contacts, entreprises et opportunités spécifiés.
    
    Optimisations :
    - N'exécute qu'une seule requête UPDATE par type d'entité pour l'ensemble des identifiants (batch).
    - Conditionne la mise à jour pour ne jamais écraser une activité plus récente (ne rétrograde pas).
    """
    from .models import Company, Contact, Deal

    target_time = target_time or timezone.now()

    if contact_ids:
        c_ids = {cid for cid in contact_ids if cid}
        if c_ids:
            Contact.objects.filter(id__in=c_ids).filter(
                Q(last_activity_at__lt=target_time) | Q(last_activity_at__isnull=True)
            ).update(last_activity_at=target_time)

    if company_ids:
        comp_ids = {cid for cid in company_ids if cid}
        if comp_ids:
            Company.objects.filter(id__in=comp_ids).filter(
                Q(last_activity_at__lt=target_time) | Q(last_activity_at__isnull=True)
            ).update(last_activity_at=target_time)

    if deal_ids:
        d_ids = {did for did in deal_ids if did}
        if d_ids:
            Deal.objects.filter(id__in=d_ids).filter(
                Q(last_activity_at__lt=target_time) | Q(last_activity_at__isnull=True)
            ).update(last_activity_at=target_time)

