# 🎯 PRD & Directives : CRM Relationnel (Indépendants & TPE)

## PROJET
Je souhaite construire un CRM minimaliste mais puissant orienté relation client et gestion de prospects (dans l'esprit d'un HubSpot de niche), l'objectif est d'éviter le piège du CRUD basique tout en écartant la lourdeur des mastodontes généralistes.

1. Modèle de Données et Fiches Unifiées (Le Socle Relationnel)
Objets relationnels interconnectés : Une architecture claire séparant Contacts (les personnes physiques), Entreprises (les comptes/organisations) et Transactions / Opportunités.   
L'Agence Copernic
Timeline d'interactions centralisée : Une vue chronologique par fiche (journal des appels, notes de réunion, e-mails échangés, et messages WhatsApp ou de messageries instantanées selon le canal de la niche).
Champs personnalisés (Custom Attributes) : Indispensable pour un CRM de niche. Le système doit permettre d'ajouter dynamiquement des métadonnées propres au secteur visé (ex: type de flotte, référence technique, surface, etc.).
2. Pipeline de Vente & Gestion des Prospects (Le Cœur Commercial)
Vue Kanban visuelle des opportunités : Un tableau défilant par étapes de vente (ex: Prospect identifié > Qualification > Démo/Offre > Négociation > Gagné / Perdu). Chaque carte doit afficher le montant, le contact rattaché et la date de dernière activité.   
Adimeo
Gestion rigoureuse des motifs de perte : Obliger l'utilisateur à renseigner une raison structurée lorsqu'un prospect est marqué comme « Perdu » (prix, timing, concurrence) pour alimenter les analyses futures.
Qualification et scoring de leads : Un indicateur visuel ou un système de règles simples pour identifier les prospects chauds à contacter en priorité.
3. Automatisation et Suivi des Activités (Productivité)
Gestion des tâches et rappels : Un module de tâches directement lié aux fiches clients (« Rappeler X pour le devis », « Envoyer la documentation technique ») avec des échéances claires.   
Blog HubSpot
Règles d'automatisation basiques (Workflows) : Déclencher des actions automatiques simples (ex: si le prospect passe à l'étape "Devis envoyé", créer une tâche de relance à J+3 ou envoyer un e-mail template).   
Adimeo
4. Pilotage et Reporting (Vue d'ensemble)
Tableau de bord de performance (Dashboard) : Une vue synthétique affichant le chiffre d'affaires prévisionnel (pipeline pondéré), le nombre de nouveaux leads sur la période, et le taux de conversion par étape.   
Adimeo
Filtres et segmentation avancée : La possibilité de segmenter la base de contacts selon des critères précis (ex: « tous les prospects n'ayant pas eu d'interaction depuis 30 jours ») pour lancer des actions ciblées.   
YellowBox CRM
5. Architecture technique et ergonomie (L'effet "Vibecoding / Moderne")
Interface fluide et réactive (type SPA / Tailwind) : Une navigation sans rechargement de page, inspirée des standards actuels (comme l'interface de Django Unfold ou de composants React modernes) pour garantir une saisie ultra-rapide sans friction.
Gestion des rôles (RBAC) : Séparer les droits entre les commerciaux (qui gèrent leur portefeuille) et les administrateurs.
Journal d'audit (Audit Logs) : Garder une trace des modifications sensibles
et respecter le principe de la multi-tenant(deux profils differentes exemple des independant ou TPE ne peuvent avoir acces aux memes données.) comme il faut sur les fiches pour garantir l'unicité des utilisateurs et la traçabilité des données.

## 🤖 RÔLE & RÈGLES STRICTES POUR L'IA (À LIRE EN PREMIER)

Tu es un **Lead Developer Fullstack Senior (Django / PostgreSQL / Astro)** spécialisé dans les architectures propres, maintenables et pragmatiques.

### Tes règles d'exécution absolues :

1. **Zéro improvisation :** Tu suis uniquement l'étape en cours dans le plan de développement ci-dessous.

2. **Une étape à la fois :** Tu ne codes JAMAIS deux fonctionnalités en même temps.

3. **Validation bloquante :** À la fin de chaque étape, tu résumes ce que tu as fait, tu indiques comment le tester, **tu t'arrêtes net et tu attends explicitement mon "GO" avant de toucher au moindre fichier de l'étape suivante.**

4. **Qualité de code :** Typage strict, bonnes pratiques Django (migrations propres, `select_related`/`prefetch_related` pour éviter les requêtes N+1), code DRY et lisible.

## 1. Vision & Stack Technique

* **Objectif :** Un CRM SaaS ultra-rapide centré sur la relation humaine et la relance commerciale pour freelances et TPE. L'utilisateur doit savoir *qui relancer aujourd'hui* en moins de 10 secondes.

* **Backend & Back-office :** Django + **Django Unfold** + **PostgreSQL**.