from datetime import timedelta
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
    Task,
    UserProfile,
)


# ------------------------------------------------------------------------------
# 1. Filtres Métiers Avancés (Scoring & Relance)
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
# 3. Administration des Tenants
# ------------------------------------------------------------------------------

@admin.register(Organization)
class OrganizationAdmin(ModelAdmin):
    list_display = ("name", "slug", "is_active", "created_at")
    search_fields = ("name", "slug")
    list_filter = ("is_active", "created_at")
    prepopulated_fields = {"slug": ("name",)}


@admin.register(UserProfile)
class UserProfileAdmin(ModelAdmin):
    list_display = ("user", "organization", "role", "created_at")
    list_filter = (
        ("organization", RelatedDropdownFilter),
        "role",
    )
    search_fields = ("user__username", "user__email", "organization__name")


# ------------------------------------------------------------------------------
# 4. Entreprises & Comptes
# ------------------------------------------------------------------------------

@admin.register(Company)
class CompanyAdmin(ModelAdmin):
    list_display = ("name", "organization", "domain", "phone", "city", "owner", "last_activity_at", "created_at")
    list_filter = (
        ("organization", RelatedDropdownFilter),
        "industry",
        ("owner", RelatedDropdownFilter),
        "created_at",
    )
    search_fields = ("name", "domain", "city", "phone")
    readonly_fields = ("created_at", "updated_at", "last_activity_at")
    inlines = [InteractionInline, TaskInline]

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("organization", "owner")


# ------------------------------------------------------------------------------
# 5. Contacts & Gestion des Prospects (Lead Scoring & Qualification)
# ------------------------------------------------------------------------------

@admin.register(Contact)
class ContactAdmin(ModelAdmin):
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
        HotLeadFilter,
        FollowUpFilter,
        ("organization", RelatedDropdownFilter),
        ("lead_status", ChoicesDropdownFilter),
        ("owner", RelatedDropdownFilter),
        ("company", RelatedDropdownFilter),
    )
    search_fields = ("first_name", "last_name", "email", "phone", "company__name")
    readonly_fields = ("created_at", "updated_at", "last_activity_at")
    inlines = [InteractionInline, TaskInline]
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
            "fields": ("custom_fields", "created_at", "updated_at")
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


# ------------------------------------------------------------------------------
# 6. Pipeline de Vente & Vue Kanban (DealAdmin)
# ------------------------------------------------------------------------------

@admin.register(Deal)
class DealAdmin(ModelAdmin):
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
        ("organization", RelatedDropdownFilter),
        ("stage", ChoicesDropdownFilter),
        ("loss_reason", ChoicesDropdownFilter),
        ("owner", RelatedDropdownFilter),
        "expected_close_date",
    )
    search_fields = ("title", "contact__last_name", "company__name")
    readonly_fields = ("created_at", "updated_at", "closed_at", "last_activity_at")
    inlines = [InteractionInline, TaskInline]

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
            "fields": ("last_activity_at", "custom_fields", "created_at", "updated_at")
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
# 7. Timeline des Interactions & Tâches
# ------------------------------------------------------------------------------

@admin.register(Interaction)
class InteractionAdmin(ModelAdmin):
    list_display = ("title", "organization", "type", "direction", "contact", "company", "deal", "user", "occurred_at")
    list_filter = (
        ("organization", RelatedDropdownFilter),
        ("type", ChoicesDropdownFilter),
        ("direction", ChoicesDropdownFilter),
        ("user", RelatedDropdownFilter),
        "occurred_at",
    )
    search_fields = ("title", "content", "contact__last_name", "company__name")
    readonly_fields = ("created_at", "updated_at")

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("organization", "contact", "company", "deal", "user")


@admin.register(Task)
class TaskAdmin(ModelAdmin):
    list_display = (
        "title",
        "organization",
        "due_date",
        "priority",
        "status",
        "assigned_to",
        "contact",
        "company",
        "deal",
    )
    list_filter = (
        ("organization", RelatedDropdownFilter),
        ("status", ChoicesDropdownFilter),
        ("priority", ChoicesDropdownFilter),
        ("assigned_to", RelatedDropdownFilter),
        "due_date",
    )
    search_fields = ("title", "description", "contact__last_name", "company__name")
    readonly_fields = ("created_at", "updated_at", "completed_at")

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("organization", "contact", "company", "deal", "assigned_to")
