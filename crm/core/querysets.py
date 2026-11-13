from datetime import timedelta
from decimal import Decimal
from typing import Optional
from django.db import models
from django.db.models import Sum, F, ExpressionWrapper, DecimalField
from django.utils import timezone


class SoftDeleteQuerySet(models.QuerySet):
    """QuerySet de base fournissant le filtrage pour la suppression douce (corbeille)."""

    def alive(self):
        """Retourne uniquement les enregistrements non supprimés."""
        return self.filter(deleted_at__isnull=True)

    def deleted(self):
        """Retourne les enregistrements placés dans la corbeille."""
        return self.filter(deleted_at__isnull=False)


class ContactQuerySet(SoftDeleteQuerySet):
    """QuerySet optimisé pour le modèle Contact."""

    def with_related(self) -> "ContactQuerySet":
        """Évite les requêtes N+1 en préchargeant l'entreprise et le responsable."""
        return self.select_related("organization", "company", "owner")

    def for_user(self, user) -> "ContactQuerySet":
        """Filtre les contacts pour un utilisateur donné (RBAC)."""
        if user.is_superuser or user.has_perm("core.view_all_contacts"):
            return self
        return self.filter(owner=user)

    def hot_leads(self, min_score: int = 50) -> "ContactQuerySet":
        """Retourne les prospects chauds basés sur le score ou le statut."""
        return self.alive().filter(
            models.Q(lead_score__gte=min_score) | models.Q(lead_status="QUALIFIED")
        )

    def needs_follow_up(self, days: int = 30) -> "ContactQuerySet":
        """Identifie les contacts sans interaction depuis un certain nombre de jours."""
        cutoff_date = timezone.now() - timedelta(days=days)
        return self.alive().filter(
            models.Q(last_activity_at__lt=cutoff_date) | models.Q(last_activity_at__isnull=True)
        )

    def inactive_for(self, days: int = 30) -> "ContactQuerySet":
        """Segment : Prospects & contacts sans interaction depuis au moins X jours (ou jamais contactés)."""
        cutoff_date = timezone.now() - timedelta(days=days)
        return self.alive().filter(
            models.Q(last_activity_at__lt=cutoff_date) | models.Q(last_activity_at__isnull=True)
        )

    def hot_unconverted(self) -> "ContactQuerySet":
        """Segment : Prospects très qualifiés (score >= 70 ou QUALIFIED) qui ne sont pas encore devenus clients."""
        return self.alive().filter(
            models.Q(lead_score__gte=70) | models.Q(lead_status="QUALIFIED")
        ).exclude(lead_status="CUSTOMER")

    def without_pending_tasks(self) -> "ContactQuerySet":
        """Segment : Contacts actifs n'ayant aucun rappel ni tâche en attente."""
        return self.alive().exclude(
            tasks__status="PENDING",
            tasks__deleted_at__isnull=True,
        )

    def new_in_period(self, days: int = 30) -> "ContactQuerySet":
        """Segment : Nouveaux contacts créés au cours des X derniers jours."""
        cutoff_date = timezone.now() - timedelta(days=days)
        return self.alive().filter(created_at__gte=cutoff_date)

    def dormant_customers(self, days: int = 60) -> "ContactQuerySet":
        """Segment : Clients existants sans échange depuis plus de X jours (risque de churn)."""
        cutoff_date = timezone.now() - timedelta(days=days)
        return self.alive().filter(
            lead_status="CUSTOMER"
        ).filter(
            models.Q(last_activity_at__lt=cutoff_date) | models.Q(last_activity_at__isnull=True)
        )


class CompanyQuerySet(SoftDeleteQuerySet):
    """QuerySet optimisé pour le modèle Company."""

    def with_related(self) -> "CompanyQuerySet":
        """Évite les requêtes N+1 en préchargeant le responsable."""
        return self.select_related("organization", "owner")

    def for_user(self, user) -> "CompanyQuerySet":
        if user.is_superuser or user.has_perm("core.view_all_companies"):
            return self
        return self.filter(owner=user)


class DealQuerySet(SoftDeleteQuerySet):
    """QuerySet optimisé pour le modèle Deal."""

    def with_related(self) -> "DealQuerySet":
        """Évite les requêtes N+1 en préchargeant contact, entreprise et responsable."""
        return self.select_related("organization", "contact", "company", "owner")

    def for_user(self, user) -> "DealQuerySet":
        if user.is_superuser or user.has_perm("core.view_all_deals"):
            return self
        return self.filter(owner=user)

    def active(self) -> "DealQuerySet":
        """Retourne les opportunités en cours dans le pipeline (non archivées/supprimées)."""
        return self.alive().exclude(stage__in=["WON", "LOST"])

    def won(self) -> "DealQuerySet":
        return self.alive().filter(stage="WON")

    def lost(self) -> "DealQuerySet":
        return self.alive().filter(stage="LOST")

    def total_pipeline_amount(self) -> Decimal:
        """Calcule le montant brut total des deals actifs."""
        result = self.active().aggregate(total=Sum("amount"))["total"]
        return result or Decimal("0.00")

    def weighted_pipeline_amount(self) -> Decimal:
        """Calcule le chiffre d'affaires prévisionnel pondéré (montant * probabilité)."""
        weighted_expr = ExpressionWrapper(
            F("amount") * F("probability") / 100.0,
            output_field=DecimalField(max_digits=14, decimal_places=2),
        )
        result = self.active().annotate(weighted=weighted_expr).aggregate(total=Sum("weighted"))["total"]
        return result or Decimal("0.00")

    def won_in_period(self, days: int = 30) -> "DealQuerySet":
        """Opportunités gagnées au cours des X derniers jours."""
        cutoff_date = timezone.now() - timedelta(days=days)
        return self.won().filter(
            models.Q(closed_at__gte=cutoff_date) | models.Q(updated_at__gte=cutoff_date)
        )

    def closing_in_period(self, days: int = 30) -> "DealQuerySet":
        """Opportunités dont la date de clôture prévisionnelle arrive sous X jours."""
        now = timezone.now().date()
        cutoff = now + timedelta(days=days)
        return self.active().filter(expected_close_date__range=[now, cutoff])

    def stalled(self, days: int = 15) -> "DealQuerySet":
        """Opportunités actives stagnantes sans mise à jour ni interaction depuis X jours."""
        cutoff_date = timezone.now() - timedelta(days=days)
        return self.active().filter(
            models.Q(last_activity_at__lt=cutoff_date) | models.Q(last_activity_at__isnull=True)
        )

    def conversion_stats(self) -> dict:
        """Calcule les taux de conversion globaux et par issue commerciale."""
        total_deals = self.alive().count()
        won_deals = self.won().count()
        lost_deals = self.lost().count()
        closed_deals = won_deals + lost_deals

        win_rate = (won_deals / closed_deals * 100) if closed_deals > 0 else 0.0
        global_conversion = (won_deals / total_deals * 100) if total_deals > 0 else 0.0

        return {
            "total_deals": total_deals,
            "won_deals": won_deals,
            "lost_deals": lost_deals,
            "win_rate": round(win_rate, 1),
            "global_conversion": round(global_conversion, 1),
        }


class InteractionQuerySet(SoftDeleteQuerySet):
    """QuerySet optimisé pour les interactions (Timeline)."""

    def with_related(self) -> "InteractionQuerySet":
        """Précharge les relations pour un affichage rapide de la timeline."""
        return self.select_related("organization", "contact", "company", "deal", "user")

    def bulk_create_and_sync(
        self,
        objs,
        batch_size: Optional[int] = None,
        ignore_conflicts: bool = False,
    ):
        """
        Insère des interactions en masse et synchronise les 'last_activity_at'
        en effectuant au maximum 1 requête UPDATE groupée par date/type d'entité,
        évitant des milliers d'écritures individuelles.
        """
        from collections import defaultdict
        from .services import sync_entities_last_activity

        objs_list = list(objs)
        for obj in objs_list:
            obj._skip_activity_sync = True

        created_objs = super().bulk_create(
            objs_list,
            batch_size=batch_size,
            ignore_conflicts=ignore_conflicts,
        )

        contact_latest = defaultdict(lambda: None)
        company_latest = defaultdict(lambda: None)
        deal_latest = defaultdict(lambda: None)

        for obj in objs_list:
            occ_time = obj.occurred_at or timezone.now()
            if obj.contact_id:
                curr = contact_latest[obj.contact_id]
                if curr is None or occ_time > curr:
                    contact_latest[obj.contact_id] = occ_time
            if obj.company_id:
                curr = company_latest[obj.company_id]
                if curr is None or occ_time > curr:
                    company_latest[obj.company_id] = occ_time
            if obj.deal_id:
                curr = deal_latest[obj.deal_id]
                if curr is None or occ_time > curr:
                    deal_latest[obj.deal_id] = occ_time

        time_to_contacts = defaultdict(set)
        for c_id, t in contact_latest.items():
            time_to_contacts[t].add(c_id)
        for t, c_ids in time_to_contacts.items():
            sync_entities_last_activity(contact_ids=c_ids, target_time=t)

        time_to_companies = defaultdict(set)
        for co_id, t in company_latest.items():
            time_to_companies[t].add(co_id)
        for t, co_ids in time_to_companies.items():
            sync_entities_last_activity(company_ids=co_ids, target_time=t)

        time_to_deals = defaultdict(set)
        for d_id, t in deal_latest.items():
            time_to_deals[t].add(d_id)
        for t, d_ids in time_to_deals.items():
            sync_entities_last_activity(deal_ids=d_ids, target_time=t)

        return created_objs


class TaskQuerySet(SoftDeleteQuerySet):
    """QuerySet optimisé pour les tâches et rappels."""

    def with_related(self) -> "TaskQuerySet":
        return self.select_related("organization", "contact", "company", "deal", "assigned_to")

    def pending(self) -> "TaskQuerySet":
        return self.alive().filter(status="PENDING")

    def completed(self) -> "TaskQuerySet":
        return self.alive().filter(status="COMPLETED")

    def overdue(self) -> "TaskQuerySet":
        """Tâches non complétées dont la date d'échéance est passée."""
        return self.alive().filter(status="PENDING", due_date__lt=timezone.now())

    def due_today(self) -> "TaskQuerySet":
        """Tâches à traiter aujourd'hui."""
        today = timezone.now().date()
        return self.alive().filter(status="PENDING", due_date__date=today)
