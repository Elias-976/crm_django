"""Gestion des rôles et contrôle d'accès basé sur les rôles (RBAC)."""

from typing import Optional
from django.contrib.auth import get_user_model
from .models import Organization, OrganizationRole, UserProfile, OrganizationMembership

User = get_user_model()


def get_user_role(user: User, organization: Optional[Organization] = None) -> str:
    """
    Détermine le rôle actif de l'utilisateur pour l'organisation spécifiée (ou son espace courant).
    Retourne OWNER, ADMIN, MEMBER, ou VIEWER.
    """
    if user.is_superuser:
        return OrganizationRole.OWNER

    target_org = organization
    if not target_org and hasattr(user, "profile"):
        target_org = user.profile.organization

    if not target_org:
        return OrganizationRole.VIEWER

    # 1. Vérifier dans OrganizationMembership
    membership = OrganizationMembership.objects.filter(
        organization=target_org,
        user=user,
        deleted_at__isnull=True,
    ).first()
    if membership:
        return membership.role

    # 2. Rétrocompatibilité avec UserProfile
    if hasattr(user, "profile") and user.profile.organization_id == target_org.id:
        return user.profile.role

    return OrganizationRole.VIEWER


def is_org_admin(user: User, organization: Optional[Organization] = None) -> bool:
    """Vérifie si l'utilisateur possède les privilèges d'administrateur ou de propriétaire sur l'organisation."""
    if user.is_superuser:
        return True
    role = get_user_role(user, organization)
    return role in [OrganizationRole.OWNER, OrganizationRole.ADMIN]


def is_org_commercial(user: User, organization: Optional[Organization] = None) -> bool:
    """Vérifie si l'utilisateur est un commercial (MEMBER) gérant son propre portefeuille."""
    if user.is_superuser:
        return False
    return get_user_role(user, organization) == OrganizationRole.MEMBER


def is_org_viewer(user: User, organization: Optional[Organization] = None) -> bool:
    """Vérifie si l'utilisateur est en lecture seule (VIEWER)."""
    if user.is_superuser:
        return False
    return get_user_role(user, organization) == OrganizationRole.VIEWER


def filter_queryset_for_role(queryset, user: User, owner_field: str = "owner"):
    """
    Applique le cloisonnement RBAC strict sur un QuerySet :
    - Superutilisateurs : vue globale sans filtre.
    - Administrateurs / Propriétaires (OWNER / ADMIN) : vue sur toutes les données du tenant.
    - Commerciaux (MEMBER) : restreint aux fiches de leur portefeuille (owner_field == user).
    - Lecteurs (VIEWER) : vue globale sur le tenant en lecture seule.
    """
    if user.is_superuser:
        return queryset

    if not hasattr(user, "profile") or not user.profile.organization:
        return queryset.none()

    org = user.profile.organization
    qs = queryset.filter(organization=org)

    if is_org_admin(user, org) or is_org_viewer(user, org):
        return qs

    # Pour un commercial, restreindre à son portefeuille personnel
    filter_kwargs = {owner_field: user}
    return qs.filter(**filter_kwargs)
