"""Moteur d'automatisation et d'exécution des workflows CRM."""

from datetime import timedelta
from typing import List, Optional
from django.core.mail import send_mail
from django.utils import timezone


def render_template_string(template_str: Optional[str], context: dict) -> str:
    """Remplace dynamiquement les variables {variable} dans une chaîne de template."""
    if not template_str:
        return ""
    result = str(template_str)
    for key, val in context.items():
        result = result.replace(f"{{{key}}}", str(val or ""))
    return result


def execute_deal_workflows(deal, old_stage: Optional[str], new_stage: str) -> List:
    """
    Déclenche les règles d'automatisation actives pour l'organisation du Deal lors d'un changement d'étape.
    Exécute la création de tâches planifiées (ex: J+3) et l'envoi d'emails templates avec log timeline.
    """
    from .models import (
        DealStage,
        Interaction,
        InteractionDirection,
        InteractionType,
        Task,
        WorkflowAction,
        WorkflowRule,
        WorkflowTrigger,
    )

    created_tasks = []
    if not deal.organization_id:
        return created_tasks

    # Sélectionner les règles actives pour ce tenant
    rules = WorkflowRule.objects.filter(
        organization_id=deal.organization_id,
        is_active=True,
    )

    context = {
        "deal_title": deal.title,
        "deal_amount": f"{deal.amount:,.2f} €",
        "contact_name": deal.contact.full_name if deal.contact else "Client",
        "company_name": deal.company.name if deal.company else "",
        "stage": deal.get_stage_display(),
    }

    for rule in rules:
        matched = False
        if rule.trigger_event == WorkflowTrigger.DEAL_STAGE_ENTERED and rule.trigger_stage == new_stage:
            matched = True
        elif rule.trigger_event == WorkflowTrigger.DEAL_WON and new_stage == DealStage.WON:
            matched = True
        elif rule.trigger_event == WorkflowTrigger.DEAL_LOST and new_stage == DealStage.LOST:
            matched = True

        if not matched:
            continue

        # 1. Action : Création automatique d'une tâche de relance
        if rule.action_type in [WorkflowAction.CREATE_TASK, WorkflowAction.CREATE_TASK_AND_EMAIL]:
            raw_title = rule.task_title or "Relance : {deal_title}"
            title = render_template_string(raw_title, context)
            due_date = timezone.now() + timedelta(days=rule.task_delay_days)

            task = Task.objects.create(
                organization_id=deal.organization_id,
                title=title,
                description=(
                    f"Rappel généré automatiquement par la règle '{rule.name}' "
                    f"suite au passage à l'étape '{deal.get_stage_display()}'."
                ),
                due_date=due_date,
                priority=rule.task_priority,
                assigned_to=deal.owner,
                contact=deal.contact,
                company=deal.company,
                deal=deal,
            )
            created_tasks.append(task)

        # 2. Action : Envoi d'un e-mail modèle & consignation dans la timeline
        if rule.action_type in [WorkflowAction.SEND_EMAIL, WorkflowAction.CREATE_TASK_AND_EMAIL]:
            if deal.contact and deal.contact.email:
                subject = render_template_string(
                    rule.email_subject or "Concernant votre opportunité {deal_title}",
                    context,
                )
                body = render_template_string(
                    rule.email_body or "Bonjour {contact_name},\n\nSuite à notre échange concernant {deal_title}...",
                    context,
                )

                try:
                    send_mail(
                        subject=subject,
                        message=body,
                        from_email=None,
                        recipient_list=[deal.contact.email],
                        fail_silently=True,
                    )
                except Exception:
                    pass

                # Trace indélébile dans la timeline chronologique d'interactions
                Interaction.objects.create(
                    organization_id=deal.organization_id,
                    type=InteractionType.EMAIL,
                    direction=InteractionDirection.OUTBOUND,
                    title=f"E-mail automatique : {subject}",
                    content=body,
                    contact=deal.contact,
                    company=deal.company,
                    deal=deal,
                    user=deal.owner,
                    occurred_at=timezone.now(),
                    metadata={"workflow_rule": rule.name, "recipient": deal.contact.email},
                )

    return created_tasks


def execute_contact_workflows(contact) -> List:
    """Déclenche les règles d'automatisation lors de la qualification d'un contact."""
    from .models import (
        Interaction,
        InteractionDirection,
        InteractionType,
        Task,
        WorkflowAction,
        WorkflowRule,
        WorkflowTrigger,
    )

    created_tasks = []
    if not contact.organization_id:
        return created_tasks

    rules = WorkflowRule.objects.filter(
        organization_id=contact.organization_id,
        is_active=True,
        trigger_event=WorkflowTrigger.CONTACT_QUALIFIED,
    )

    context = {
        "contact_name": contact.full_name,
        "company_name": contact.company.name if contact.company else "",
    }

    for rule in rules:
        if rule.action_type in [WorkflowAction.CREATE_TASK, WorkflowAction.CREATE_TASK_AND_EMAIL]:
            raw_title = rule.task_title or "Appel de cadrage : {contact_name}"
            title = render_template_string(raw_title, context)
            due_date = timezone.now() + timedelta(days=rule.task_delay_days)

            task = Task.objects.create(
                organization_id=contact.organization_id,
                title=title,
                description=f"Action automatique suite à la qualification du prospect '{contact.full_name}'.",
                due_date=due_date,
                priority=rule.task_priority,
                assigned_to=contact.owner,
                contact=contact,
                company=contact.company,
            )
            created_tasks.append(task)

        if rule.action_type in [WorkflowAction.SEND_EMAIL, WorkflowAction.CREATE_TASK_AND_EMAIL]:
            if contact.email:
                subject = render_template_string(
                    rule.email_subject or "Bienvenue {contact_name}",
                    context,
                )
                body = render_template_string(
                    rule.email_body or "Bonjour {contact_name},\n\nRavi d'échanger prochainement avec vous.",
                    context,
                )
                try:
                    send_mail(
                        subject=subject,
                        message=body,
                        from_email=None,
                        recipient_list=[contact.email],
                        fail_silently=True,
                    )
                except Exception:
                    pass

                Interaction.objects.create(
                    organization_id=contact.organization_id,
                    type=InteractionType.EMAIL,
                    direction=InteractionDirection.OUTBOUND,
                    title=f"E-mail automatique : {subject}",
                    content=body,
                    contact=contact,
                    company=contact.company,
                    user=contact.owner,
                    occurred_at=timezone.now(),
                    metadata={"workflow_rule": rule.name, "recipient": contact.email},
                )

    return created_tasks

