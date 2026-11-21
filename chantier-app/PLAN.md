# Plan de travail : application de gestion de chantiers

Application web et mobile (PWA) pour un bureau d'entreprise BTP : gestion des
chantiers, contrôle des matériaux contre les fuites de stock, et pointage des
ouvriers.

## Décisions prises

| Sujet | Décision |
|---|---|
| Approche | Nouvelle application indépendante (pas de reprise du code d'Afribat, sous licence AGPL) |
| Technique | FastAPI + SQLAlchemy + PostgreSQL (backend), React (frontend), PWA |
| Dépassement du métré | Demande bloquée : motif obligatoire, puis validation par le bureau, avec alerte |
| Pointage | Le chef de chantier pointe toute son équipe depuis son téléphone, avec position GPS |
| Support | Web pour le bureau, mobile installable pour le chantier |
| Pays | Plusieurs pays : chaque chantier a sa devise et son fuseau horaire |
| Langues | Français et anglais, au choix de chaque utilisateur |
| Tolérance d'inventaire | Paramétrable : valeur par défaut, modifiable par chantier et par matériau |
| Correction du pointage | Le chef peut corriger aujourd'hui et hier ; ensuite, seul le bureau |
| Avancement | Saisi par le chef, validé par le conducteur de travaux |
| Hébergement | Cloud, fournisseur choisi à la mise en service ; photos et documents dans un stockage en ligne |

## Rôles

| Rôle | Peut faire |
|---|---|
| Bureau (admin) | Tout : comptes, paramètres, tous les chantiers, toutes les alertes |
| Conducteur de travaux | Ses chantiers : métré, planning, budget, validation des demandes hors métré |
| Chef de chantier | Son chantier : demandes de matériaux, réception, pose, inventaire, pointage, journal |

---

## Où on en est

| | État |
|---|---|
| Modèle de données, règles de stock et de pointage, API (`backend/`) | ✅ Écrits |
| Tests des règles de stock (`backend/tests/test_fuite_stock.py`) | ✍️ Écrits, pas encore lancés |
| Tout le reste | ⏳ À faire |

---

## Étape 1 : vérifier ce qui est écrit

- [ ] Lancer les tests des règles de stock et corriger les erreurs.
- [ ] Écrire et lancer les tests du pointage : double pointage, pointage loin du chantier,
      anciens jours bloqués, ouvrier non affecté, calcul des heures.
- [ ] Tester les droits : un chef ne voit que ses chantiers, ne modifie pas le métré,
      ne valide pas ses propres demandes, n'efface pas les alertes.

## Étape 2 : compléter les fonctions

### Déjà écrit (à vérifier à l'étape 1)

- **Chantiers et ouvrages** (ex. « Salle de bain RDC »).
- **Métré** : quantité nette + taux de perte = quantité autorisée par ouvrage et par matériau.
- **Détection des fuites de stock**, en trois contrôles :
  1. *À la demande* : au-delà de la quantité autorisée, la demande est bloquée.
     Motif obligatoire, validation par le bureau, alerte. Les demandes en attente
     comptent aussi, pour qu'on ne puisse pas contourner la limite en découpant.
  2. *À la pose* : alerte si on pose plus que prévu sur un ouvrage ; impossible de
     sortir plus que le stock présent.
  3. *À l'inventaire* : stock compté comparé au stock calculé (livré − posé).
     Manquant au-delà de la tolérance = alerte avec la valeur en argent.
- **Pointage** : présent, retard, demi-journée, absent, congé, maladie ; heures calculées ;
  un seul pointage par ouvrier et par jour, tous chantiers confondus ; position GPS
  comparée au chantier ; pas de correction des anciens jours par le chef.
- **Récapitulatif de pointage** : jours, heures, coût ; export CSV pour Excel ;
  le chef voit les jours, pas les salaires.
- **Alertes et tableau de bord.**

### Fonctions de gestion à ajouter

| Fonction | Ce qu'elle fait | Contrôle |
|---|---|---|
| Avancement | Le chef propose chaque semaine le % fait par ouvrage ; le conducteur valide ou corrige. Historique conservé. Le budget et les alertes utilisent la valeur validée | Alerte si les matériaux posés ne correspondent pas à l'avancement (ex. 60 % du carrelage posé pour 30 % fait) |
| Planning | Dates prévues et réelles par ouvrage | Retards en rouge |
| Budget | Prévu (métré × prix + main-d'œuvre prévue) comparé au dépensé (livraisons + journées pointées) | Alerte si on dépense plus vite qu'on avance |
| Journal de chantier | Une page par jour : météo, travaux, incidents, photos ; effectif repris du pointage | Non modifiable après quelques jours |
| Commandes fournisseurs | Fournisseurs, bons de commande regroupant les demandes acceptées, prix, date, suivi (commandé, livré en partie, livré), PDF | Réception faite contre la commande |
| Documents | Plans, contrat, devis, procès-verbaux, classés par chantier | Accès selon le rôle (le chef ne voit pas contrats et prix) |

### Paramétrage à ajouter

- **Plusieurs pays** : devise et fuseau horaire sur chaque chantier (pointage, journal,
  budget et valeurs de stock dans la devise du chantier). Les totaux qui regroupent
  plusieurs chantiers sont présentés par devise, sans conversion automatique.
- **Tolérance d'inventaire paramétrable** sur trois niveaux : valeur par défaut de
  l'entreprise, remplacée si besoin par une valeur propre au chantier, puis au matériau
  (ex. le sable en vrac tolère plus d'écart que le carrelage).
- **Français et anglais** : tous les textes de l'interface, des alertes, des messages
  d'erreur et des exports existent dans les deux langues ; chaque utilisateur choisit la
  sienne.
- **Écran « Paramètres »** pour le bureau : devise, fuseau horaire, tolérance d'inventaire,
  délai de correction du pointage, horaires et pause habituels, seuils de gravité des
  alertes, taux de perte par défaut par type de matériau, nom, logo et coordonnées de
  l'entreprise. Ces réglages quittent le fichier `.env`, qui ne garde que la base de
  données, la clé de sécurité et l'adresse du site.
- **Assistant de démarrage** au premier lancement : nom et logo de l'entreprise, devise,
  fuseau horaire, nouveau mot de passe administrateur, création des premiers comptes.
  Le mot de passe par défaut ne peut pas rester actif.
- **Import Excel** des matériaux, des ouvriers et du métré : modèle de fichier
  téléchargeable, aperçu avant enregistrement avec les erreurs ligne par ligne
  (unité inconnue, prix manquant, doublon).
- **Modèle de chantier** : créer un chantier en copiant les ouvrages, le métré et le
  planning d'un chantier existant ou d'un modèle (ex. « Villa R+1 type »).

## Étape 3 : l'interface web et mobile

- **Écrans de base** : connexion, tableau de bord, chantiers, métré, suivi des matériaux
  (prévu, demandé, livré, posé), demandes (avec le reste disponible affiché pendant la
  saisie), validation, stock et inventaire, pointage, récapitulatif, ouvriers, matériaux,
  utilisateurs.
- **Écrans de gestion** : avancement, planning, budget, journal, commandes, fournisseurs,
  documents.
- **Écrans de paramétrage** : paramètres, assistant de démarrage, import Excel, création
  de chantier depuis un modèle.

## Étape 4 : usage sur chantier

- Installation sur l'écran d'accueil du téléphone.
- Pointage et journal hors connexion, envoyés quand le réseau revient.
- Photos prises avec le téléphone et compressées automatiquement.
- Vérification de l'affichage sur petit écran.

## Étape 5 : mise en service

- Docker : application + base PostgreSQL, installable chez n'importe quel hébergeur cloud.
- Photos et documents dans un stockage en ligne compatible S3 (proposé par la plupart des
  hébergeurs), avec un stockage sur disque pour les tests.
- Guide d'installation et guide de démarrage en français.
- Données de démonstration (chantier, métré de salle de bain, ouvriers).
- Sauvegarde automatique de la base et des photos.

## Plus tard (deuxième version)

- Facturation du client (situations de travaux).
- Sous-traitants.
- Sécurité (HSE).
- Engins et matériel.
- Notifications SMS, WhatsApp ou e-mail pour les alertes critiques.
- Pointage individuel des ouvriers (QR code ou badge).

---

## Paramétrage à faire à l'installation

| Réglage technique (`.env`) | À faire |
|---|---|
| `APP_DATABASE_URL` | **Obligatoire** : adresse de la base PostgreSQL |
| `APP_SECRET_KEY` | **Obligatoire** : clé secrète longue et aléatoire |
| `APP_ADMIN_EMAIL`, `APP_ADMIN_PASSWORD` | **Obligatoire** : compte administrateur de départ |
| `APP_CORS_ORIGINS` | Adresse réelle du site |

Ensuite, dans l'application, dans cet ordre : les utilisateurs, le catalogue des
matériaux, les fournisseurs, les ouvriers. Puis, pour chaque chantier : la fiche (avec la
position GPS), les ouvrages, le métré, le planning et le budget, l'affectation des
ouvriers et les documents.

## Questions ouvertes

Aucune : les six questions ont reçu une réponse (voir « Décisions prises »).
Reste à choisir l'hébergeur cloud au moment de la mise en service.
