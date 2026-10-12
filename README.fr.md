<p align="center">
  <a href="README.md">English</a> · <a href="README.zh-CN.md">简体中文</a> · <a href="README.zh-TW.md">繁體中文</a> · <a href="README.ja.md">日本語</a> · <a href="README.ko.md">한국어</a><br>
  <a href="README.de.md">Deutsch</a> · <a href="README.es.md">Español</a> · <strong>Français</strong> · <a href="README.pt-BR.md">Português (Brasil)</a> · <a href="README.vi.md">Tiếng Việt</a> · <a href="README.id.md">Bahasa Indonesia</a> · <a href="README.it.md">Italiano</a>
</p>

<h1 align="center">SuperLive AI Monitor</h1>
<p align="center">Choisissez un polygone. Enregistrez l’activité des personnes. Recevez les vidéos sur Telegram.</p>
<p align="center"> <img src="https://img.shields.io/badge/platform-Windows-0078D6" alt="Windows"> <img src="https://img.shields.io/badge/Python-3.12-3776AB" alt="Python 3.12"> <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-green" alt="MIT License"></a> </p>
<p align="center"><a href="#quick-start">Démarrage rapide</a> · <a href="#workflow">Fonctionnement</a> · <a href="#features">Fonctions</a> · <a href="#demo">Démonstration</a> · <a href="#faq">Questions fréquentes</a> · <a href="#documentation">Documentation</a></p>

SuperLive AI Monitor ajoute l’enregistrement par événement à l’image de SuperLive affichée dans MuMu sous Windows. Sélectionnez une zone polygonale : YOLO y détecte les personnes, puis les vidéos terminées sont envoyées sur Telegram. Aucune adresse RTSP de caméra n’est nécessaire.

Il s’agit d’un projet communautaire non officiel, sans affiliation avec les fournisseurs de SuperLive ou de MuMu. Les douze langues du README concernent la documentation ; l’interface du programme utilise actuellement le chinois traditionnel.

<a id="quick-start"></a>
## Démarrage rapide

1. Installez MuMu et SuperLive, connectez-vous et ouvrez la vue en direct à surveiller. Gardez la session Windows ouverte.
2. Clonez ou téléchargez ce dépôt. Lancez `installer\INSTALL_FROM_REPO.bat` ou la commande ci-dessous.
3. Configurez votre propre bot et votre conversation localement avec `installer\SET_TELEGRAM.bat`. Le dépôt ne contient aucun identifiant secret.
4. Exécutez `installer\VERIFY_ALL.bat` avec la vue en direct ouverte.
5. Lancez `%USERPROFILE%\Downloads\SuperLive_AI\Start_AI_Monitor.bat` ; utilisez `Stop_AI_Monitor.bat` pour arrêter.

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\installer\INSTALL_FROM_REPO.ps1
```

**Déjà installé ?** Lancez `installer\UPDATE_FROM_REPO.bat`. La mise à jour sauvegarde puis arrête les programmes, conserve la zone et les réglages locaux de Telegram et du stockage, puis laisse la surveillance à l’arrêt. Vérifiez l’image et redémarrez manuellement. Aucun démarrage automatique Windows, aucune tâche planifiée et aucun service ne sont créés.

<a id="workflow"></a>
## Fonctionnement

```text
Nouvelle activité d’une personne dans le polygone
  → démarrer l’enregistrement ; bloquer tout nouvel envoi automatique Telegram pendant 120 secondes
  → poursuivre la détection pendant tout l’enregistrement
  → mouvement à partir de 120 secondes et avant 300 : terminer la vidéo et démarrer immédiatement la suivante
  → chaque nouvelle vidéo impose une nouvelle pause d’envoi de 120 secondes
  → terminer aussi après 120 secondes sans mouvement ou à la limite de 300 secondes
  → placer la vidéo terminée dans la file Telegram
  → continuer à surveiller ; reprendre sur un nouveau mouvement ou une nouvelle arrivée
```

Les vidéos normales durent **entre 120 et 300 secondes**. Une personne observée pour la première fois au démarrage compte comme une nouvelle activité. L’absence et l’immobilité comptent toutes deux comme une absence de mouvement ; chaque mouvement détecté relance le délai d’inactivité. Une fois les premières 120 secondes écoulées, un mouvement déclenche également le passage immédiat à la vidéo suivante. Une personne restant immobile ne déclenche pas immédiatement une nouvelle vidéo après la précédente.

Aucune photo Telegram n’est envoyée au démarrage : les envois automatiques concernent uniquement les vidéos terminées. La pause d’envoi de chaque nouvelle vidéo bloque aussi les anciennes vidéos et leurs nouvelles tentatives d’envoi. Des changements de vidéo répétés peuvent retarder toute la file. Ce sont les nouveaux appels d’envoi qui sont bloqués ; un transfert déjà lancé ne peut pas être rappelé et peut arriver pendant la pause.

La sélection d’une zone ou l’absence de résultats valides de capture ou de détection interrompt l’observation. Cette période inconnue ne compte pas comme de l’inactivité : un nouvel intervalle commence après le rétablissement.

<a id="features"></a>
## Fonctions

| Fonction | Comportement |
| --- | --- |
| Zone de détection | Polygones convexes ou concaves ; compatibilité avec les anciens rectangles |
| Prise en compte d’une personne | Son cadre doit chevaucher la zone sur une surface positive ; un simple contact avec le bord ne suffit pas |
| Image de caméra | Exclut le titre et les menus de MuMu ; conserve les horodatages et noms de canal présents dans l’image |
| Enregistrement | Normalement 2–5 minutes ; dès 120 secondes, un mouvement démarre immédiatement la vidéo suivante ; arrêt aussi après 120 secondes sans mouvement |
| Alertes et envoi | Alertes locales Windows ; seules les vidéos H.264 MP4 terminées passent par Telegram ; tous les envois attendent pendant les premières 120 secondes de chaque vidéo |
| Exploitation et stockage | Superviseurs distincts et nouvelles tentatives de capture ; 10 FPS et `C:\SuperLive_Recordings` par défaut |

À 80 % d’occupation du disque d’enregistrement, les plus anciennes vidéos éligibles sont supprimées jusqu’à revenir à 78 %. L’enregistrement actif et les fichiers protégés en attente d’envoi sont conservés. La vidéo contient toute l’image de caméra recadrée, sans découpage à la forme du polygone.

<a id="controls"></a>
## Sélectionner le polygone

| Touche ou action | Effet |
| --- | --- |
| R / Sélectionner | Commencer une zone |
| Clic gauche | Ajouter au moins trois sommets distincts dans l’ordre du contour |
| Clic droit / Retour arrière | Retirer le dernier sommet |
| Entrée / Enregistrer | Fermer et enregistrer un polygone valide dont les côtés ne se croisent pas |
| Échap / Annuler | Conserver la zone précédente |
| C / Image entière | Supprimer la restriction de zone |
| Q / Arrêter | Arrêter la surveillance et finaliser la vidéo en cours |

Les raccourcis nécessitent que la fenêtre d’aperçu soit active. Les coordonnées sont enregistrées relativement au contenu de la caméra. Si une ancienne zone ne peut pas être adaptée au recadrage sans ambiguïté, sélectionnez-la de nouveau ou choisissez explicitement l’image entière.

<a id="demo"></a>
## Démonstration

Ces exemples **muets de 36 secondes** utilisent le véritable moteur d’affichage de l’aperçu avec une scène synthétique, des cadres de personnes prédéfinis et des états d’enregistrement simulés. Les légendes chinoises et anglaises montrent la pause d’envoi, le changement de vidéo et l’attente d’une nouvelle activité. Ce ne sont pas des images de caméra réelles ni une preuve de précision de YOLO. Les intervalles de 120 secondes sont raccourcis pour la démonstration.

![Démonstration synthétique du polygone et de l’enregistrement d’activité](docs/media/superlive-demo.gif)

[Ouvrir ou télécharger la démonstration MP4](docs/media/superlive-demo.mp4)

| Choisir la zone | Enregistrer l’activité | Attendre un nouveau mouvement |
| --- | --- | --- |
| ![Sélection des sommets](docs/media/polygon-selection.png) | ![Enregistrement de l’activité](docs/media/activity-recording.png) | ![Surveillance après inactivité](docs/media/inactivity-rearm.png) |

<a id="faq"></a>
## Questions fréquentes

**Pourquoi une personne immobile ne déclenche-t-elle pas une nouvelle vidéo ?** Il faut un nouveau mouvement ou une nouvelle arrivée pour lancer une autre vidéo. Au démarrage du programme, la première observation compte comme une activité. Aucune alerte de début n’est envoyée sur Telegram.

**Comment le mouvement est-il déterminé ?** Par les changements de position ou de taille du cadre de la personne. Les faibles variations sont filtrées et les déplacements lents s’accumulent. Il ne s’agit pas d’une analyse de posture : un mouvement qui ne modifie pas le cadre peut passer inaperçu. Les déplacements de caméra, les occultations et les erreurs de détection peuvent aussi influencer le résultat.

**Chaque vidéo dure-t-elle cinq minutes ?** Non. Elle dure normalement entre 2 et 5 minutes. Un mouvement à partir de la seconde 120 et avant la seconde 300 termine la vidéo et démarre immédiatement la suivante ; 120 secondes sans mouvement la terminent également. Un arrêt manuel ou une panne peuvent produire un fragment plus court, conservé localement. Aucun envoi ne commence avant la fin de la pause encore en vigueur.

**Est-ce compatible avec VMware et les coupures de Telegram ?** L’inférence sur CPU est possible ; l’accélération graphique de VMware ne fournit pas CUDA. Les vidéos dont l’envoi échoue restent dans la file et font l’objet de nouvelles tentatives.

<a id="documentation"></a>
## Documentation et sécurité

[Guide en anglais](docs/USER_GUIDE.md) · [Guide en chinois traditionnel](docs/USER_GUIDE.zh-TW.md) · [Architecture](docs/ARCHITECTURE.md) · [Conception de l’enregistrement](docs/RECORDING.md) · [Modifications](CHANGELOG.md) · [Sécurité](SECURITY.md)

Le code se trouve dans `src/`, les modèles publics de configuration dans `config/` et les outils d’installation dans `installer/`. Vérifiez la vraie caméra et l’envoi Telegram dans votre environnement ; les tests hors ligne ne peuvent pas valider cette configuration.

Ne publiez jamais de véritables jetons, identifiants de conversation, réglages locaux, journaux, images de caméra ou sauvegardes privées. Avant l’envoi des modifications, exécutez `python scripts/security_scan.py .`.

## Licence

[Licence MIT](LICENSE).
