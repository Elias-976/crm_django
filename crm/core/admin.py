from django.contrib import admin
from .models import Company, Contact, Deal, Interaction, Organization, Task, UserProfile


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "is_active", "created_at")
    search_fields = ("name", "slug")
    list_filter = ("is_active", "created_at")
    prepopulated_fields = {"slug": ("name",)}


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "organization", "role", "created_at")
    list_filter = ("organization", "role")
    search_fields = ("user__username", "user__email", "organization__name")


class InteractionInline(admin.TabularInline):
    model = Interaction
    extra = 0
    fields = ("type", "direction", "title", "occurred_at", "user")
    readonly_fields = ("occurred_at",)
    ordering = ("-occurred_at",)


class TaskInline(admin.TabularInline):
    model = Task
    extra = 0
    fields = ("title", "due_date", "priority", "status", "assigned_to")
    ordering = ("due_date",)


@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    list_display = ("name", "organization", "domain", "phone", "city", "owner", "last_activity_at", "created_at")
    list_filter = ("organization", "industry", "owner", "created_at")
    search_fields = ("name", "domain", "city", "phone")
    readonly_fields = ("created_at", "updated_at", "last_activity_at")
    inlines = [InteractionInline, TaskInline]

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("organization", "owner")


@admin.register(Contact)
class ContactAdmin(admin.ModelAdmin):
    list_display = (
        "full_name",
        "organization",
        "company",
        "email",
        "phone",
        "lead_status",
        "lead_score",
        "owner",
        "last_activity_at",
    )
    list_filter = ("organization", "lead_status", "owner", "company")
    search_fields = ("first_name", "last_name", "email", "phone")
    readonly_fields = ("created_at", "updated_at", "last_activity_at")
    inlines = [InteractionInline, TaskInline]

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("organization", "company", "owner")


@admin.register(Deal)
class DealAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "organization",
        "amount",
        "stage",
        "probability",
        "weighted_amount",
        "contact",
        "company",
        "owner",
        "expected_close_date",
    )
    list_filter = ("organization", "stage", "owner", "expected_close_date", "loss_reason")
    search_fields = ("title", "contact__last_name", "company__name")
    readonly_fields = ("created_at", "updated_at", "closed_at", "last_activity_at")
    inlines = [InteractionInline, TaskInline]

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("organization", "contact", "company", "owner")


@admin.register(Interaction)
class InteractionAdmin(admin.ModelAdmin):
    list_display = ("title", "organization", "type", "direction", "contact", "company", "deal", "user", "occurred_at")
    list_filter = ("organization", "type", "direction", "occurred_at", "user")
    search_fields = ("title", "content", "contact__last_name", "company__name")
    readonly_fields = ("created_at", "updated_at")

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("organization", "contact", "company", "deal", "user")


@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    list_display = ("title", "organization", "due_date", "priority", "status", "assigned_to", "contact", "company", "deal")
    list_filter = ("organization", "status", "priority", "due_date", "assigned_to")
    search_fields = ("title", "description", "contact__last_name", "company__name")
    readonly_fields = ("created_at", "updated_at", "completed_at")

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("organization", "contact", "company", "deal", "assigned_to")
