from datetime import timedelta
from decimal import Decimal
from django.contrib import admin, messages
from django.db import models
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import path, reverse_lazy
from django.utils import timezone
from unfold.admin import ModelAdmin, TabularInline
from unfold.contrib.filters.admin import (
    ChoicesDropdownFilter,
    MultipleRelatedDropdownFilter,
    RelatedDropdownFilter,
)
from unfold.decorators import action, display

from .models import (
    Company,
    Contact,
    Deal,
    DealStage,
    Interaction,
    LeadStatus,
    LossReason,
    Organization,
    OrganizationMembership,
    OrganizationRole,
    Task,
    TaskPriority,
    TaskStatus,
    UserProfile,
    WorkflowAction,
    WorkflowRule,
    WorkflowTrigger,
)


# ------------------------------------------------------------------------------
# 1. Filtres Métiers Avancés (Scoring, Relance & Tâches)
# ------------------------------------------------------------------------------

class HotLeadFilter(admin.SimpleListFilter):
    title = "Maturité Commerciale (Lead Score)"
    parameter_name = "lead_maturity"

    def lookups(self, request, model_admin):
        return [
            ("hot", "🔥 Prospects chauds (Score >= 70)"),
            ("warm", "⚡ Prospects tièdes (30 à 69)"),
            ("cold", "❄️ Prospects froids (< 30)"),
        ]

    def queryset(self, request, queryset):
        if self.value() == "hot":
            return queryset.filter(lead_score__gte=70)
        if self.value() == "warm":
            return queryset.filter(lead_score__gte=30, lead_score__lt=70)
        if self.value() == "cold":
            return queryset.filter(lead_score__lt=30)
        return queryset


class FollowUpFilter(admin.SimpleListFilter):
    title = "Urgence de Relance"
    parameter_name = "follow_up_status"

    def lookups(self, request, model_admin):
        return [
            ("urgent", "⚠️ En retard (> 30 jours sans contact)"),
            ("recent", "✅ Actif (interaction <= 7 jours)"),
        ]

    def queryset(self, request, queryset):
        now = timezone.now()
        if self.value() == "urgent":
            cutoff = now - timedelta(days=30)
            return queryset.filter(
                models.Q(last_activity_at__lt=cutoff) | models.Q(last_activity_at__isnull=True)
            )
        if self.value() == "recent":
            cutoff = now - timedelta(days=7)
            return queryset.filter(last_activity_at__gte=cutoff)
        return queryset


class TaskDueFilter(admin.SimpleListFilter):
    title = "Échéance du Rappel"
    parameter_name = "due_status"

    def lookups(self, request, model_admin):
        return [
            ("overdue", "🚨 En retard"),
            ("today", "⭐ À traiter aujourd'hui"),
            ("week", "📅 Cette semaine"),
            ("upcoming", "⏳ À venir"),
            ("completed", "✅ Terminées"),
        ]

    def queryset(self, request, queryset):
        now = timezone.now()
        today = now.date()
        if self.value() == "overdue":
            return queryset.filter(status=TaskStatus.PENDING, due_date__lt=now)
        if self.value() == "today":
            return queryset.filter(status=TaskStatus.PENDING, due_date__date=today)
        if self.value() == "week":
            end_of_week = today + timedelta(days=7)
            return queryset.filter(status=TaskStatus.PENDING, due_date__date__range=[today, end_of_week])
        if self.value() == "upcoming":
            return queryset.filter(status=TaskStatus.PENDING, due_date__gt=now)
        if self.value() == "completed":
            return queryset.filter(status=TaskStatus.COMPLETED)
        return queryset


class TrashFilter(admin.SimpleListFilter):
    title = "État d'archivage (Corbeille)"
    parameter_name = "is_deleted"

    def lookups(self, request, model_admin):
        return [
            ("alive", "🟢 Actifs"),
            ("trash", "🗑️ Corbeille (Supprimés)"),
        ]

    def queryset(self, request, queryset):
        if self.value() == "alive":
            return queryset.filter(deleted_at__isnull=True)
        if self.value() == "trash":
            return queryset.filter(deleted_at__isnull=False)
        return queryset


class ContactSegmentFilter(admin.SimpleListFilter):
    title = "🎯 Segmentation Stratégique"
    parameter_name = "segment"

    def lookups(self, request, model_admin):
        return [
            ("inactive_30d", "⚠️ Inactifs (> 30 jours sans contact)"),
            ("hot_unconverted", "🔥 Prospects chauds non convertis"),
            ("no_pending_tasks", "🚨 Sans tâche de relance prévue"),
            ("new_30d", "⭐ Nouveaux (30 derniers jours)"),
            ("dormant_customers", "💤 Clients dormants (> 60 jours)"),
        ]

    def queryset(self, request, queryset):
        val = self.value()
        if val == "inactive_30d":
            return queryset.inactive_for(days=30)
        if val == "hot_unconverted":
            return queryset.hot_unconverted()
        if val == "no_pending_tasks":
            return queryset.without_pending_tasks()
        if val == "new_30d":
            return queryset.new_in_period(days=30)
        if val == "dormant_customers":
            return queryset.dormant_customers(days=60)
        return queryset


class DealSegmentFilter(admin.SimpleListFilter):
    title = "🎯 Cibles Pipeline & Échéance"
    parameter_name = "deal_segment"

    def lookups(self, request, model_admin):
        return [
            ("closing_soon", "⏳ Clôture ce mois-ci"),
            ("stalled", "⚠️ Opportunités stagnantes (> 15j)"),
            ("high_value", "💎 Opportunités majeures (≥ 10 000 €)"),
            ("won_recent", "🏆 Gagnées récemment (30j)"),
        ]

    def queryset(self, request, queryset):
        val = self.value()
        if val == "closing_soon":
            return queryset.closing_in_period(days=30)
        if val == "stalled":
            return queryset.stalled(days=15)
        if val == "high_value":
            return queryset.active().filter(amount__gte=Decimal("10000.00"))
        if val == "won_recent":
            return queryset.won_in_period(days=30)
        return queryset


class SoftDeleteAdminMixin:
    """Mixin apportant les actions de corbeille et de restauration rapide."""

    actions = ["soft_delete_selected_action", "restore_selected_action"]

    @action(description="Mettre à la corbeille 🗑️", icon="delete")
    def soft_delete_selected_action(self, request, queryset):
        count = 0
        for obj in queryset:
            if hasattr(obj, "soft_delete"):
                obj.soft_delete()
                count += 1
        messages.success(request, f"{count} élément(s) déplacé(s) dans la corbeille.")

    @action(description="Restaurer depuis la corbeille ♻️", icon="restore_from_trash")
    def restore_selected_action(self, request, queryset):
        count = 0
        for obj in queryset:
            if hasattr(obj, "restore"):
                obj.restore()
                count += 1
        messages.success(request, f"{count} élément(s) restauré(s) avec succès.")


# ------------------------------------------------------------------------------
# 2. Inlines Unfold
# ------------------------------------------------------------------------------

class InteractionInline(TabularInline):
    model = Interaction
    extra = 0
    fields = ("type", "direction", "title", "occurred_at", "user")
    readonly_fields = ("occurred_at",)
    ordering = ("-occurred_at",)


class TaskInline(TabularInline):
    model = Task
    extra = 0
    fields = ("title", "due_date", "priority", "status", "assigned_to")
    ordering = ("due_date",)


# ------------------------------------------------------------------------------
# 3. Administration des Tenants & Espaces de Travail
# ------------------------------------------------------------------------------

@admin.register(Organization)
class OrganizationAdmin(SoftDeleteAdminMixin, ModelAdmin):
    list_display = ("name", "slug", "is_active", "created_at")
    search_fields = ("name", "slug")
    list_filter = ("is_active", TrashFilter, "created_at")
    prepopulated_fields = {"slug": ("name",)}
    readonly_fields = ("uuid", "created_at", "updated_at", "deleted_at")


@admin.register(UserProfile)
class UserProfileAdmin(ModelAdmin):
    list_display = ("user", "organization", "role", "created_at")
    list_filter = (
        ("organization", RelatedDropdownFilter),
        "role",
    )
    search_fields = ("user__username", "user__email", "organization__name")


@admin.register(OrganizationMembership)
class OrganizationMembershipAdmin(SoftDeleteAdminMixin, ModelAdmin):
    list_display = ("user", "organization", "role", "is_default", "created_at")
    list_filter = (
        ("organization", RelatedDropdownFilter),
        "role",
        "is_default",
        TrashFilter,
    )
    search_fields = ("user__username", "user__email", "organization__name")
    readonly_fields = ("uuid", "created_at", "updated_at", "deleted_at")


# ------------------------------------------------------------------------------
# 4. Entreprises & Comptes
# ------------------------------------------------------------------------------

@admin.register(Company)
class CompanyAdmin(SoftDeleteAdminMixin, ModelAdmin):
    list_display = ("name", "organization", "domain", "phone", "city", "owner", "last_activity_at", "created_at")
    list_filter = (
        ("organization", RelatedDropdownFilter),
        "industry",
        ("owner", RelatedDropdownFilter),
        TrashFilter,
        "created_at",
    )
    search_fields = ("name", "domain", "city", "phone")
    readonly_fields = ("uuid", "created_at", "updated_at", "last_activity_at", "deleted_at")
    inlines = [InteractionInline, TaskInline]

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("organization", "owner")


# ------------------------------------------------------------------------------
# 5. Contacts & Gestion des Prospects (Lead Scoring & Qualification)
# ------------------------------------------------------------------------------

@admin.register(Contact)
class ContactAdmin(SoftDeleteAdminMixin, ModelAdmin):
    list_display = (
        "full_name",
        "display_lead_score",
        "display_lead_status",
        "organization",
        "company",
        "email",
        "phone",
        "display_follow_up_urgency",
        "owner",
    )
    list_filter = (
        ContactSegmentFilter,
        HotLeadFilter,
        FollowUpFilter,
        ("organization", RelatedDropdownFilter),
        ("lead_status", ChoicesDropdownFilter),
        ("owner", RelatedDropdownFilter),
        ("company", RelatedDropdownFilter),
        TrashFilter,
    )
    search_fields = ("first_name", "last_name", "email", "phone", "company__name")
    readonly_fields = ("uuid", "created_at", "updated_at", "last_activity_at", "deleted_at")
    inlines = [InteractionInline, TaskInline]
    actions = SoftDeleteAdminMixin.actions + ["batch_schedule_followup_action", "export_segment_csv_action"]
    actions_row = ["qualify_lead_action", "recalculate_score_action"]

    fieldsets = (
        ("Identité & Organisation", {
            "fields": ("organization", "first_name", "last_name", "job_title", "company", "owner")
        }),
        ("Coordonnées de Relance", {
            "fields": ("email", "phone")
        }),
        ("Qualification Commerciale", {
            "fields": ("lead_status", "lead_score", "last_activity_at")
        }),
        ("Champs Spécifiques / Métadonnées", {
            "classes": ("collapse",),
            "fields": ("uuid", "custom_fields", "created_at", "updated_at", "deleted_at")
        }),
    )

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("organization", "company", "owner")

    @display(
        description="Maturité / Score",
        ordering="lead_score",
        label={
            "HOT": "success",
            "WARM": "warning",
            "COLD": "info",
        },
    )
    def display_lead_score(self, obj):
        score = obj.lead_score
        if score >= 70:
            return "HOT", f"🔥 {score} (Chaud)"
        elif score >= 30:
            return "WARM", f"⚡ {score} (Tiède)"
        return "COLD", f"❄️ {score} (Froid)"

    @display(
        description="Statut Commercial",
        ordering="lead_status",
        label={
            LeadStatus.NEW: "info",
            LeadStatus.CONTACTED: "warning",
            LeadStatus.QUALIFIED: "success",
            LeadStatus.UNQUALIFIED: "danger",
            LeadStatus.CUSTOMER: "success",
        },
    )
    def display_lead_status(self, obj):
        return obj.lead_status, obj.get_lead_status_display()

    @display(description="Dernier Contact", ordering="last_activity_at")
    def display_follow_up_urgency(self, obj):
        if not obj.last_activity_at:
            return "⚠️ Jamais contacté"
        days = (timezone.now() - obj.last_activity_at).days
        if days > 30:
            return f"⚠️ Inactif ({days}j)"
        return f"{obj.last_activity_at:%d/%m/%Y} ({days}j)"

    @action(description="Qualifier le prospect (+30 pts)", icon="verified")
    def qualify_lead_action(self, request, object_id):
        contact = self.get_object(request, object_id)
        if contact:
            contact.lead_status = LeadStatus.QUALIFIED
            contact.lead_score = max(contact.lead_score + 30, 70)
            contact.save()
            messages.success(request, f"Le prospect '{contact.full_name}' a été qualifié (Score: {contact.lead_score}).")
        return redirect("admin:core_contact_changelist")

    @action(description="Recalculer le score de maturité", icon="calculate")
    def recalculate_score_action(self, request, object_id):
        contact = self.get_object(request, object_id)
        if contact:
            score = 0
            if contact.email:
                score += 20
            if contact.phone:
                score += 25
            if contact.job_title:
                score += 15
            if contact.company:
                score += 20
            if contact.interactions.exists():
                score += 20
            contact.lead_score = min(score, 100)
            contact.save()
            messages.success(request, f"Score de '{contact.full_name}' mis à jour : {contact.lead_score}/100.")
        return redirect("admin:core_contact_changelist")

    @action(description="Planifier une relance groupée (J+2) 📅", icon="notification_add")
    def batch_schedule_followup_action(self, request, queryset):
        count = 0
        due_date = timezone.now() + timedelta(days=2)
        for contact in queryset:
            Task.objects.create(
                organization_id=contact.organization_id,
                title=f"Relance ciblée : {contact.full_name}",
                description="Rappel généré automatiquement via la segmentation stratégique.",
                due_date=due_date,
                priority=TaskPriority.HIGH if contact.lead_score >= 70 else TaskPriority.MEDIUM,
                assigned_to=contact.owner or request.user,
                contact=contact,
                company=contact.company,
            )
            count += 1
        self.message_user(request, f"{count} tâche(s) de relance planifiée(s) à J+2 pour les contacts sélectionnés.")

    @action(description="Exporter le segment en CSV 📥", icon="download")
    def export_segment_csv_action(self, request, queryset):
        import csv
        from django.http import HttpResponse

        response = HttpResponse(content_type="text/csv; charset=utf-8-sig")
        response["Content-Disposition"] = 'attachment; filename="segment_contacts_crm.csv"'

        writer = csv.writer(response, delimiter=";")
        writer.writerow([
            "UUID",
            "Nom complet",
            "Prénom",
            "Nom",
            "Email",
            "Téléphone",
            "Entreprise",
            "Poste",
            "Statut Lead",
            "Score",
            "Dernière Activité",
            "Organisation",
        ])

        for c in queryset.select_related("company", "organization"):
            writer.writerow([
                str(c.uuid),
                c.full_name,
                c.first_name,
                c.last_name,
                c.email or "",
                c.phone or "",
                c.company.name if c.company else "",
                c.job_title or "",
                c.get_lead_status_display(),
                c.lead_score,
                c.last_activity_at.strftime("%d/%m/%Y %H:%M") if c.last_activity_at else "Aucune",
                c.organization.name if c.organization else "",
            ])
        return response


# ------------------------------------------------------------------------------
# 6. Pipeline de Vente & Vue Kanban (DealAdmin)
# ------------------------------------------------------------------------------

@admin.register(Deal)
class DealAdmin(SoftDeleteAdminMixin, ModelAdmin):
    list_display = (
        "title",
        "display_amount",
        "display_stage",
        "display_weighted_amount",
        "organization",
        "contact",
        "company",
        "display_loss_info",
        "owner",
        "expected_close_date",
    )
    list_filter = (
        DealSegmentFilter,
        ("organization", RelatedDropdownFilter),
        ("stage", ChoicesDropdownFilter),
        ("loss_reason", ChoicesDropdownFilter),
        ("owner", RelatedDropdownFilter),
        "expected_close_date",
        TrashFilter,
    )
    search_fields = ("title", "contact__last_name", "company__name")
    readonly_fields = ("uuid", "created_at", "updated_at", "closed_at", "last_activity_at", "deleted_at")
    inlines = [InteractionInline, TaskInline]

    actions = SoftDeleteAdminMixin.actions + ["export_pipeline_csv_action"]
    actions_list = ["kanban_view_action"]
    actions_row = ["advance_stage_action", "mark_as_won_action"]

    fieldsets = (
        ("Opportunité & Pipeline", {
            "fields": ("organization", "title", "amount", "stage", "probability", "expected_close_date")
        }),
        ("Rattachement Relationnel", {
            "fields": ("contact", "company", "owner")
        }),
        ("Clôture & Gestion des Pertes (Obligatoire si Perdu)", {
            "description": "Renseignez obligatoirement le motif en cas d'abandon pour alimenter l'analyse commerciale.",
            "fields": ("closed_at", "loss_reason", "loss_notes")
        }),
        ("Suivi & Métadonnées", {
            "classes": ("collapse",),
            "fields": ("uuid", "last_activity_at", "custom_fields", "created_at", "updated_at", "deleted_at")
        }),
    )

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("organization", "contact", "company", "owner")

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                "kanban/",
                self.admin_site.admin_view(self.kanban_view),
                name="core_deal_kanban",
            ),
            path(
                "<int:deal_id>/move-stage/",
                self.admin_site.admin_view(self.move_stage_view),
                name="core_deal_move_stage",
            ),
        ]
        return custom_urls + urls

    @display(description="Montant Brut", ordering="amount")
    def display_amount(self, obj):
        return f"{obj.amount:,.2f} €".replace(",", " ").replace(".", ",")

    @display(
        description="Étape Pipeline",
        ordering="stage",
        label={
            DealStage.LEAD: "info",
            DealStage.QUALIFIED: "primary",
            DealStage.PROPOSAL: "warning",
            DealStage.NEGOTIATION: "purple",
            DealStage.WON: "success",
            DealStage.LOST: "danger",
        },
    )
    def display_stage(self, obj):
        return obj.stage, obj.get_stage_display()

    @display(description="Prévisionnel (Pondéré)", ordering="probability")
    def display_weighted_amount(self, obj):
        return f"{obj.weighted_amount:,.2f} € ({obj.probability}%)".replace(",", " ").replace(".", ",")

    @display(description="Motif Perte", ordering="loss_reason")
    def display_loss_info(self, obj):
        if obj.stage == DealStage.LOST and obj.loss_reason:
            return f"❌ {obj.get_loss_reason_display()}"
        return "-"

    @action(description="Vue Kanban Pipeline", icon="view_kanban", url_path="kanban")
    def kanban_view_action(self, request):
        return redirect("admin:core_deal_kanban")

    @action(description="Étape suivante ➡️", icon="arrow_forward")
    def advance_stage_action(self, request, object_id):
        deal = self.get_object(request, object_id)
        if not deal:
            messages.error(request, "Opportunité introuvable.")
            return redirect("admin:core_deal_changelist")

        order = [
            DealStage.LEAD,
            DealStage.QUALIFIED,
            DealStage.PROPOSAL,
            DealStage.NEGOTIATION,
            DealStage.WON,
        ]
        if deal.stage in order:
            idx = order.index(deal.stage)
            if idx < len(order) - 1:
                deal.stage = order[idx + 1]
                deal.save()
                messages.success(request, f"'{deal.title}' est maintenant à l'étape '{deal.get_stage_display()}'.")
            else:
                messages.info(request, "Cette opportunité a déjà atteint l'étape Gagnée.")
        else:
            messages.warning(request, "Impossible d'avancer une opportunité marquée comme Perdue.")
        return redirect("admin:core_deal_changelist")

    @action(description="Marquer Gagné 🏆", icon="emoji_events")
    def mark_as_won_action(self, request, object_id):
        deal = self.get_object(request, object_id)
        if deal:
            deal.stage = DealStage.WON
            deal.closed_at = timezone.now()
            deal.save()
            messages.success(request, f"Opportunité '{deal.title}' marquée comme Gagnée 🏆 !")
        return redirect("admin:core_deal_changelist")

    @action(description="Exporter le pipeline en CSV 📥", icon="download")
    def export_pipeline_csv_action(self, request, queryset):
        import csv
        from django.http import HttpResponse

        response = HttpResponse(content_type="text/csv; charset=utf-8-sig")
        response["Content-Disposition"] = 'attachment; filename="pipeline_opportunites_crm.csv"'

        writer = csv.writer(response, delimiter=";")
        writer.writerow([
            "UUID",
            "Titre",
            "Montant (€)",
            "Étape",
            "Probabilité (%)",
            "Montant Pondéré (€)",
            "Contact",
            "Entreprise",
            "Responsable",
            "Date Clôture Prévue",
            "Motif Perte",
            "Organisation",
        ])

        for d in queryset.select_related("contact", "company", "owner", "organization"):
            writer.writerow([
                str(d.uuid),
                d.title,
                f"{d.amount:.2f}",
                d.get_stage_display(),
                d.probability,
                f"{d.weighted_amount:.2f}",
                d.contact.full_name if d.contact else "",
                d.company.name if d.company else "",
                d.owner.username if d.owner else "",
                d.expected_close_date.strftime("%d/%m/%Y") if d.expected_close_date else "",
                d.get_loss_reason_display() if d.loss_reason else "",
                d.organization.name if d.organization else "",
            ])
        return response

    def kanban_view(self, request):
        """Vue interactive Kanban défilante par étapes de vente."""
        org_id = request.GET.get("org")
        queryset = Deal.objects.select_related("contact", "company", "owner", "organization")

        if org_id:
            queryset = queryset.filter(organization_id=org_id)
        elif not request.user.is_superuser and hasattr(request.user, "profile"):
            queryset = queryset.filter(organization=request.user.profile.organization)

        stages_data = []
        for stage_code, stage_label in DealStage.choices:
            stage_deals = [d for d in queryset if d.stage == stage_code]
            total_stage = sum(d.amount for d in stage_deals)
            stages_data.append({
                "code": stage_code,
                "label": stage_label,
                "deals": stage_deals,
                "count": len(stage_deals),
                "total_amount": total_stage,
            })

        active_deals = queryset.exclude(stage__in=[DealStage.WON, DealStage.LOST])
        total_pipeline = sum(d.amount for d in active_deals)
        weighted_pipeline = sum(d.weighted_amount for d in active_deals)

        context = {
            **self.admin_site.each_context(request),
            "title": "Pipeline Commercial - Vue Kanban",
            "stages": stages_data,
            "total_deals": queryset.count(),
            "total_pipeline": total_pipeline,
            "weighted_pipeline": weighted_pipeline,
            "organizations": Organization.objects.all(),
            "selected_org": org_id,
            "opts": self.model._meta,
        }
        return render(request, "admin/core/deal/kanban.html", context)

    def move_stage_view(self, request, deal_id):
        """Action rapide de transition d'étape depuis le tableau Kanban."""
        if request.method != "POST":
            return redirect("admin:core_deal_kanban")

        deal = get_object_or_404(Deal, pk=deal_id)
        action_type = request.POST.get("action")

        order = [
            DealStage.LEAD,
            DealStage.QUALIFIED,
            DealStage.PROPOSAL,
            DealStage.NEGOTIATION,
            DealStage.WON,
        ]

        if action_type == "advance":
            if deal.stage in order:
                idx = order.index(deal.stage)
                if idx < len(order) - 1:
                    deal.stage = order[idx + 1]
                    deal.save()
                    messages.success(request, f"'{deal.title}' est passé à l'étape '{deal.get_stage_display()}'.")
        elif action_type == "win":
            deal.stage = DealStage.WON
            deal.closed_at = timezone.now()
            deal.save()
            messages.success(request, f"Félicitations ! '{deal.title}' est marqué comme Gagné 🏆.")

        return redirect("admin:core_deal_kanban")


# ------------------------------------------------------------------------------
# 7. Timeline des Interactions
# ------------------------------------------------------------------------------

@admin.register(Interaction)
class InteractionAdmin(SoftDeleteAdminMixin, ModelAdmin):
    list_display = ("title", "organization", "type", "direction", "contact", "company", "deal", "user", "occurred_at")
    list_filter = (
        ("organization", RelatedDropdownFilter),
        ("type", ChoicesDropdownFilter),
        ("direction", ChoicesDropdownFilter),
        ("user", RelatedDropdownFilter),
        "occurred_at",
        TrashFilter,
    )
    search_fields = ("title", "content", "contact__last_name", "company__name")
    readonly_fields = ("uuid", "created_at", "updated_at", "deleted_at")

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("organization", "contact", "company", "deal", "user")


# ------------------------------------------------------------------------------
# 8. Module de Tâches & Relances (Productivité)
# ------------------------------------------------------------------------------

@admin.register(Task)
class TaskAdmin(SoftDeleteAdminMixin, ModelAdmin):
    list_display = (
        "title",
        "display_due_status",
        "display_priority",
        "display_status",
        "assigned_to",
        "contact",
        "deal",
        "company",
        "organization",
    )
    list_filter = (
        TaskDueFilter,
        ("organization", RelatedDropdownFilter),
        ("status", ChoicesDropdownFilter),
        ("priority", ChoicesDropdownFilter),
        ("assigned_to", RelatedDropdownFilter),
        "due_date",
        TrashFilter,
    )
    search_fields = ("title", "description", "contact__last_name", "company__name")
    readonly_fields = ("uuid", "created_at", "updated_at", "completed_at", "deleted_at")
    actions_row = ["complete_task_action", "postpone_one_day_action", "postpone_three_days_action"]

    fieldsets = (
        ("Détails du Rappel & Priorité", {
            "fields": ("organization", "title", "description", "due_date", "priority", "status", "assigned_to")
        }),
        ("Rattachement Relationnel", {
            "fields": ("contact", "company", "deal")
        }),
        ("Horodatage & Réalisation", {
            "classes": ("collapse",),
            "fields": ("uuid", "completed_at", "created_at", "updated_at", "deleted_at")
        }),
    )

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("organization", "contact", "company", "deal", "assigned_to")

    @display(description="Échéance & Retard", ordering="due_date")
    def display_due_status(self, obj):
        now = timezone.now()
        if obj.status == TaskStatus.COMPLETED:
            return f"✅ Fait ({obj.completed_at:%d/%m})" if obj.completed_at else "✅ Fait"
        if obj.due_date < now:
            days = (now - obj.due_date).days
            return f"🚨 Retard ({days}j)"
        if obj.due_date.date() == now.date():
            return "⭐ Aujourd'hui"
        days = (obj.due_date.date() - now.date()).days
        return f"⏳ Dans {days}j ({obj.due_date:%d/%m})"

    @display(
        description="Priorité",
        ordering="priority",
        label={
            TaskPriority.URGENT: "danger",
            TaskPriority.HIGH: "warning",
            TaskPriority.MEDIUM: "primary",
            TaskPriority.LOW: "info",
        },
    )
    def display_priority(self, obj):
        return obj.priority, obj.get_priority_display()

    @display(
        description="Statut",
        ordering="status",
        label={
            TaskStatus.PENDING: "warning",
            TaskStatus.COMPLETED: "success",
            TaskStatus.CANCELLED: "info",
        },
    )
    def display_status(self, obj):
        return obj.status, obj.get_status_display()

    @action(description="Terminer ✅", icon="check_circle")
    def complete_task_action(self, request, object_id):
        task = self.get_object(request, object_id)
        if task:
            task.mark_completed(log_interaction=True)
            messages.success(request, f"Tâche '{task.title}' marquée comme terminée ✅ et consignée dans l'historique.")
        return redirect("admin:core_task_changelist")

    @action(description="+1 jour 📅", icon="event")
    def postpone_one_day_action(self, request, object_id):
        task = self.get_object(request, object_id)
        if task:
            task.due_date += timedelta(days=1)
            task.save(update_fields=["due_date", "updated_at"])
            messages.success(request, f"Tâche '{task.title}' reportée au {task.due_date:%d/%m/%Y}.")
        return redirect("admin:core_task_changelist")

    @action(description="+3 jours 📅", icon="update")
    def postpone_three_days_action(self, request, object_id):
        task = self.get_object(request, object_id)
        if task:
            task.due_date += timedelta(days=3)
            task.save(update_fields=["due_date", "updated_at"])
            messages.success(request, f"Tâche '{task.title}' reportée au {task.due_date:%d/%m/%Y}.")
        return redirect("admin:core_task_changelist")


# ------------------------------------------------------------------------------
# 9. Règles d'Automatisation (Workflows)
# ------------------------------------------------------------------------------

@admin.register(WorkflowRule)
class WorkflowRuleAdmin(SoftDeleteAdminMixin, ModelAdmin):
    list_display = (
        "name",
        "organization",
        "display_trigger",
        "display_action",
        "is_active",
        "created_at",
    )
    list_filter = (
        ("organization", RelatedDropdownFilter),
        ("trigger_event", ChoicesDropdownFilter),
        ("action_type", ChoicesDropdownFilter),
        "is_active",
        TrashFilter,
    )
    search_fields = ("name", "task_title", "email_subject")
    readonly_fields = ("uuid", "created_at", "updated_at", "deleted_at")
    actions_row = ["toggle_active_action"]

    fieldsets = (
        ("Configuration de la règle", {
            "fields": ("organization", "name", "is_active")
        }),
        ("Déclencheur d'Événement", {
            "description": "L'action sera exécutée automatiquement dès que cet événement se produit.",
            "fields": ("trigger_event", "trigger_stage")
        }),
        ("Action Automatique", {
            "fields": ("action_type",)
        }),
        ("Paramètres de la Tâche de Relance (si applicable)", {
            "description": "Variables de remplacement disponibles : {deal_title}, {contact_name}, {company_name}, {deal_amount}",
            "fields": ("task_title", "task_delay_days", "task_priority")
        }),
        ("Paramètres de l'E-mail Modèle (si applicable)", {
            "description": "L'e-mail envoyé est automatiquement consigné dans la timeline des interactions du contact.",
            "fields": ("email_subject", "email_body")
        }),
    )

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("organization")

    @display(description="Déclencheur", ordering="trigger_event")
    def display_trigger(self, obj):
        if obj.trigger_event == WorkflowTrigger.DEAL_STAGE_ENTERED and obj.trigger_stage:
            return f"⚡ Étape Deal : {obj.get_trigger_stage_display()}"
        return f"⚡ {obj.get_trigger_event_display()}"

    @display(description="Action Exécutée", ordering="action_type")
    def display_action(self, obj):
        if obj.action_type == WorkflowAction.CREATE_TASK:
            return f"📋 Tâche à J+{obj.task_delay_days}"
        elif obj.action_type == WorkflowAction.SEND_EMAIL:
            return "✉️ Envoi d'e-mail modèle"
        return f"📋 Tâche (J+{obj.task_delay_days}) + ✉️ E-mail"

    @action(description="Activer / Désactiver", icon="toggle_on")
    def toggle_active_action(self, request, object_id):
        rule = self.get_object(request, object_id)
        if rule:
            rule.is_active = not rule.is_active
            rule.save(update_fields=["is_active", "updated_at"])
            state = "activée" if rule.is_active else "désactivée"
            messages.success(request, f"La règle '{rule.name}' a été {state}.")
        return redirect("admin:core_workflowrule_changelist")
