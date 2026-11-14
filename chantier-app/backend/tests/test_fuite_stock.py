"""Détection des fuites de stock : demande, consommation, inventaire."""


def demande(api, e, quantite, **extra):
    return api.post(
        f"/api/chantiers/{e.ch['id']}/demandes",
        {"ouvrage_id": e.sdb["id"], "materiau_id": e.carrelage["id"], "quantite": quantite, **extra},
    )


def test_verification_affiche_le_reste(env):
    r = env.chef.post(
        f"/api/chantiers/{env.ch['id']}/demandes/verifier",
        {"ouvrage_id": env.sdb["id"], "materiau_id": env.carrelage["id"], "quantite": 4},
    )
    assert r.status_code == 200
    v = r.json()
    assert v["quantite_autorisee"] == 11.0  # 10 m² + 10 % de perte
    assert v["reste"] == 11.0
    assert v["dans_le_metre"] is True


def test_demande_dans_le_metre_est_approuvee_automatiquement(env):
    r = demande(env.chef, env, 8)
    assert r.status_code == 201, r.text
    assert r.json()["statut"] == "approuvee"
    assert r.json()["numero"] == 1


def test_demande_au_dela_du_metre_est_bloquee_sans_motif(env):
    demande(env.chef, env, 8)
    r = demande(env.chef, env, 5)  # il ne reste que 3 m²
    assert r.status_code == 422
    body = r.json()
    assert body["code"] == "depassement_metre"
    assert "il reste 3 m²" in body["detail"]
    assert body["verification"]["depassement"] == 2.0


def test_decoupage_en_petites_demandes_ne_contourne_pas_la_limite(env):
    assert demande(env.chef, env, 6).json()["statut"] == "approuvee"
    assert demande(env.chef, env, 5).json()["statut"] == "approuvee"  # total 11 = limite
    assert demande(env.chef, env, 0.5).status_code == 422  # plus rien


def test_depassement_avec_motif_part_en_validation_et_cree_une_alerte(env):
    demande(env.chef, env, 11)
    r = demande(env.chef, env, 2, motif_depassement="3 carreaux cassés à la livraison")
    assert r.status_code == 201
    d = r.json()
    assert d["statut"] == "en_attente"
    assert d["depassement"] == 2.0

    alertes = env.cond.get("/api/alertes").json()
    assert len(alertes) == 1
    assert alertes[0]["type"] == "depassement_metre"
    assert alertes[0]["gravite"] == "attention"  # 13 / 11 = +18 %

    a_valider = env.cond.get("/api/demandes/a-valider").json()
    assert [x["id"] for x in a_valider] == [d["id"]]


def test_demandes_en_attente_comptent_dans_l_engage(env):
    demande(env.chef, env, 11)
    demande(env.chef, env, 2, motif_depassement="casse")
    # une seconde demande hors métré voit les 2 m² en attente
    r = demande(env.chef, env, 1, motif_depassement="encore")
    assert r.json()["deja_engage"] == 13.0


def test_materiau_non_prevu_au_metre_demande_toujours_une_validation(env):
    r = env.chef.post(
        f"/api/chantiers/{env.ch['id']}/demandes",
        {"ouvrage_id": env.sdb["id"], "materiau_id": env.colle["id"], "quantite": 1},
    )
    assert r.status_code == 422
    assert "pas prévu au métré" in r.json()["detail"]


def test_le_chef_ne_peut_pas_valider_sa_propre_demande(env):
    demande(env.chef, env, 11)
    d = demande(env.chef, env, 2, motif_depassement="casse").json()
    r = env.chef.post(f"/api/demandes/{d['id']}/decision", {"approuver": True})
    assert r.status_code == 403


def test_refus_exige_une_raison_et_libere_la_quantite(env):
    demande(env.chef, env, 11)
    d = demande(env.chef, env, 2, motif_depassement="casse").json()
    assert env.cond.post(f"/api/demandes/{d['id']}/decision", {"approuver": False}).status_code == 422
    r = env.cond.post(f"/api/demandes/{d['id']}/decision", {"approuver": False, "commentaire": "Voir le stock"})
    assert r.json()["statut"] == "refusee"
    v = env.chef.post(
        f"/api/chantiers/{env.ch['id']}/demandes/verifier",
        {"ouvrage_id": env.sdb["id"], "materiau_id": env.carrelage["id"], "quantite": 1},
    ).json()
    assert v["deja_engage"] == 11.0


def test_un_chef_ne_voit_pas_les_chantiers_des_autres(env):
    assert env.chef2.get(f"/api/chantiers/{env.ch['id']}").status_code == 404
    assert demande(env.chef2, env, 1).status_code == 404
    assert [c["id"] for c in env.chef2.get("/api/chantiers").json()] == [env.ch2["id"]]


def receptionner_11(env):
    d = demande(env.chef, env, 11).json()
    r = env.chef.post(f"/api/demandes/{d['id']}/reception", {"quantite_livree": 11})
    assert r.status_code == 200, r.text
    return d


def test_reception_alimente_le_stock(env):
    receptionner_11(env)
    stock = env.chef.get(f"/api/chantiers/{env.ch['id']}/stock").json()
    assert stock[0]["quantite"] == 11.0
    assert stock[0]["valeur"] == 220.0


def test_reception_superieure_a_l_approuve_refusee(env):
    d = demande(env.chef, env, 5).json()
    r = env.chef.post(f"/api/demandes/{d['id']}/reception", {"quantite_livree": 6})
    assert r.status_code == 422


def test_consommation_limitee_au_stock(env):
    receptionner_11(env)
    r = env.chef.post(
        f"/api/chantiers/{env.ch['id']}/sorties",
        {"materiau_id": env.carrelage["id"], "ouvrage_id": env.sdb["id"], "quantite": 12},
    )
    assert r.status_code == 422
    assert "Stock insuffisant" in r.json()["detail"]


def test_surconsommation_sur_un_ouvrage_declenche_une_alerte(env):
    # Le bureau valide un complément : 14 m² livrés pour 11 prévus.
    receptionner_11(env)
    d = demande(env.chef, env, 3, motif_depassement="casse").json()
    env.cond.post(f"/api/demandes/{d['id']}/decision", {"approuver": True})
    env.chef.post(f"/api/demandes/{d['id']}/reception", {"quantite_livree": 3})

    sortie = lambda q: env.chef.post(  # noqa: E731
        f"/api/chantiers/{env.ch['id']}/sorties",
        {"materiau_id": env.carrelage["id"], "ouvrage_id": env.sdb["id"], "quantite": q},
    )
    assert sortie(10).status_code == 201
    assert [a for a in env.cond.get("/api/alertes").json() if a["type"] == "surconsommation"] == []
    assert sortie(3).status_code == 201  # 13 posés pour 11 prévus
    sur = [a for a in env.cond.get("/api/alertes").json() if a["type"] == "surconsommation"]
    assert len(sur) == 1
    assert "+2 m²" in sur[0]["titre"]


def test_inventaire_detecte_un_manquant_et_recale_le_stock(env):
    receptionner_11(env)
    env.chef.post(
        f"/api/chantiers/{env.ch['id']}/sorties",
        {"materiau_id": env.carrelage["id"], "ouvrage_id": env.sdb["id"], "quantite": 4},
    )
    # théorique : 7 m² ; compté : 4 m² -> 3 m² manquants (43 %, 60 €)
    r = env.chef.post(
        f"/api/chantiers/{env.ch['id']}/inventaires",
        {"lignes": [{"materiau_id": env.carrelage["id"], "quantite_comptee": 4}]},
    )
    assert r.status_code == 201, r.text
    ligne = r.json()["lignes"][0]
    assert ligne["quantite_theorique"] == 7.0
    assert ligne["ecart"] == -3.0
    assert ligne["valeur_ecart"] == -60.0

    alertes = [a for a in env.cond.get("/api/alertes").json() if a["type"] == "ecart_inventaire"]
    assert len(alertes) == 1
    assert alertes[0]["gravite"] == "critique"
    assert "60" in alertes[0]["titre"]

    stock = env.chef.get(f"/api/chantiers/{env.ch['id']}/stock").json()
    assert stock[0]["quantite"] == 4.0


def test_petit_ecart_d_inventaire_dans_la_tolerance_sans_alerte(env):
    d = demande(env.chef, env, 11).json()
    env.chef.post(f"/api/demandes/{d['id']}/reception", {"quantite_livree": 11})
    env.chef.post(
        f"/api/chantiers/{env.ch['id']}/inventaires",
        {"lignes": [{"materiau_id": env.carrelage["id"], "quantite_comptee": 10.9}]},  # -0,9 %
    )
    assert [a for a in env.cond.get("/api/alertes").json() if a["type"] == "ecart_inventaire"] == []


def test_suivi_materiaux(env):
    receptionner_11(env)
    env.chef.post(
        f"/api/chantiers/{env.ch['id']}/sorties",
        {"materiau_id": env.carrelage["id"], "ouvrage_id": env.sdb["id"], "quantite": 5.5},
    )
    suivi = env.chef.get(f"/api/chantiers/{env.ch['id']}/suivi-materiaux").json()
    assert len(suivi) == 1
    s = suivi[0]
    assert (s["autorise"], s["demande"], s["livre"], s["pose"], s["reste_a_demander"]) == (11, 11, 11, 5.5, 0)
    assert s["pct_pose"] == 50.0
    assert s["hors_metre"] is False


def test_le_chef_ne_peut_pas_modifier_le_metre(env):
    r = env.chef.post(
        f"/api/chantiers/{env.ch['id']}/metre",
        {"ouvrage_id": env.sdb["id"], "materiau_id": env.colle["id"], "quantite_nette": 100},
    )
    assert r.status_code == 403


def test_le_chef_ne_peut_pas_effacer_une_alerte(env):
    demande(env.chef, env, 11)
    demande(env.chef, env, 2, motif_depassement="casse")
    alerte = env.chef.get("/api/alertes").json()[0]
    assert env.chef.post(f"/api/alertes/{alerte['id']}/lue").status_code == 403
    assert env.cond.post(f"/api/alertes/{alerte['id']}/lue").json()["lue"] is True
