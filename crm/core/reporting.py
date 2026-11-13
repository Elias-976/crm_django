"""Moteur de calcul des métriques commerciales, reporting et tableaux de bord (KPIs)."""

from datetime import timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional
from django.db.models import Count, Sum, F, Q
from django.utils import timezone

from .models import (
    Contact,
    Company,
    Deal,
    DealStage,
    Interaction,
    LeadStatus,
    LossReason,
    Organization,
    Task,
    TaskStatus,
)


def get_crm_kpi_summary(
    organization_id: Optional[int] = None,
    days: int = 30,
) -> Dict[str, Any]:
    """
    Calcule l'ensemble des indicateurs clés de performance (KPIs) pour le CRM.
    Cloisonné par organisation (multi-tenant) ou global si organization_id est None (superadmin).
    """
    # 1. Base QuerySets cloisonnés
    deals_qs = Deal.objects.alive()
    contacts_qs = Contact.objects.alive()
    tasks_qs = Task.objects.alive()

    if organization_id:
        deals_qs = deals_qs.filter(organization_id=organization_id)
        contacts_qs = contacts_qs.filter(organization_id=organization_id)
        tasks_qs = tasks_qs.filter(organization_id=organization_id)

    # 2. Indicateurs Financiers du Pipeline
    weighted_pipeline = deals_qs.weighted_pipeline_amount()
    total_active_pipeline = deals_qs.total_pipeline_amount()

    # Deals gagnés sur la période
    cutoff_date = timezone.now() - timedelta(days=days)
    won_in_period_qs = deals_qs.won().filter(
        Q(closed_at__gte=cutoff_date) | Q(updated_at__gte=cutoff_date)
    )
    won_revenue_period = (
        won_in_period_qs.aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
    )
    won_deals_count_period = won_in_period_qs.count()

    # 3. Métriques d'Acquisition & Nouveaux Leads
    new_leads_period = contacts_qs.filter(created_at__gte=cutoff_date).count()
    total_contacts_count = contacts_qs.count()
    hot_leads_count = contacts_qs.hot_unconverted().count()

    # 4. Taux de Conversion & Funnel de Vente
    conversion_stats = deals_qs.conversion_stats()

    # Analyse détaillée par étape de vente
    funnel_data: List[Dict[str, Any]] = []
    total_active_amount = total_active_pipeline if total_active_pipeline > 0 else Decimal("1.00")

    for stage_code, stage_label in DealStage.choices:
        stage_deals = deals_qs.filter(stage=stage_code)
        count = stage_deals.count()
        total_amt = stage_deals.aggregate(total=Sum("amount"))["total"] or Decimal("0.00")

        # Pourcentage du montant par rapport au total actif (pour les étapes du pipeline actif)
        pct = 0.0
        if stage_code not in [DealStage.WON, DealStage.LOST] and total_active_pipeline > 0:
            pct = round(float((total_amt / total_active_amount) * 100), 1)

        funnel_data.append({
            "code": stage_code,
            "label": stage_label,
            "count": count,
            "amount": total_amt,
            "percentage": pct,
        })

    # 5. Analyse des Motifs de Perte (Loss Reasons)
    lost_deals = deals_qs.lost()
    total_lost_count = lost_deals.count()
    loss_reasons_summary = []

    for reason_code, reason_label in LossReason.choices:
        r_deals = lost_deals.filter(loss_reason=reason_code)
        r_count = r_deals.count()
        if r_count > 0:
            r_amount = r_deals.aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
            pct = round((r_count / total_lost_count * 100), 1) if total_lost_count > 0 else 0.0
            loss_reasons_summary.append({
                "code": reason_code,
                "label": reason_label,
                "count": r_count,
                "amount": r_amount,
                "percentage": pct,
            })
    loss_reasons_summary.sort(key=lambda x: x["count"], reverse=True)

    # 6. Urgences & Relances ("Qui relancer aujourd'hui en moins de 10s")
    now = timezone.now()
    overdue_tasks_qs = tasks_qs.overdue().select_related("contact", "company", "deal", "assigned_to")
    today_tasks_qs = tasks_qs.due_today().select_related("contact", "company", "deal", "assigned_to")

    top_urgent_tasks = list(
        overdue_tasks_qs.order_by("due_date", "-priority")[:5]
    ) + list(
        today_tasks_qs.order_by("priority")[:5]
    )
    # Déduplication tout en conservant l'ordre
    seen_task_ids = set()
    deduped_tasks = []
    for t in top_urgent_tasks:
        if t.id not in seen_task_ids:
            seen_task_ids.add(t.id)
            deduped_tasks.append(t)

    # Deals à clôturer ce mois-ci
    top_closing_deals = list(
        deals_qs.closing_in_period(days=30)
        .select_related("contact", "company", "owner")
        .order_by("-amount")[:5]
    )

    # 7. Compteurs de Segments Avancés
    segments_summary = {
        "inactive_30d": contacts_qs.inactive_for(days=30).count(),
        "hot_unconverted": hot_leads_count,
        "without_pending_tasks": contacts_qs.without_pending_tasks().count(),
        "dormant_customers": contacts_qs.dormant_customers(days=60).count(),
        "stalled_deals": deals_qs.stalled(days=15).count(),
    }

    return {
        "days": days,
        "weighted_pipeline": weighted_pipeline,
        "total_active_pipeline": total_active_pipeline,
        "won_revenue_period": won_revenue_period,
        "won_deals_count_period": won_deals_count_period,
        "new_leads_period": new_leads_period,
        "total_contacts_count": total_contacts_count,
        "hot_leads_count": hot_leads_count,
        "win_rate": conversion_stats["win_rate"],
        "global_conversion": conversion_stats["global_conversion"],
        "total_deals_count": conversion_stats["total_deals"],
        "funnel_data": funnel_data,
        "loss_reasons_summary": loss_reasons_summary,
        "overdue_tasks_count": overdue_tasks_qs.count(),
        "today_tasks_count": today_tasks_qs.count(),
        "top_urgent_tasks": deduped_tasks[:6],
        "top_closing_deals": top_closing_deals,
        "segments_summary": segments_summary,
    }


def unfold_dashboard_callback(request, context: dict) -> dict:
    """Callback officiel pour enrichir la vue d'accueil Django Unfold (/admin/)."""
    org_id = None
    if not request.user.is_superuser and hasattr(request.user, "profile"):
        org_id = request.user.profile.organization_id
    elif "org" in request.GET:
        try:
            org_id = int(request.GET.get("org"))
        except (ValueError, TypeError):
            pass

    # Durée d'analyse sélectionnée
    days = 30
    if "days" in request.GET:
        try:
            days = int(request.GET.get("days"))
        except (ValueError, TypeError):
            pass

    kpis = get_crm_kpi_summary(organization_id=org_id, days=days)

    context.update({
        "kpis": kpis,
        "organizations": Organization.objects.alive() if request.user.is_superuser else None,
        "selected_org": str(org_id) if org_id else "",
        "selected_days": days,
    })
    return context
