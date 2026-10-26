# Confidentialité et publication

Les fichiers `.env`, bases, journaux, sessions, captures réseau, clés de chiffrement et configurations privées réelles ne doivent jamais être versionnés. Seuls les modèles `.env.example` et `immoalert.example.php`, sans valeur réelle, sont publics. Les rapports opérationnels, sauvegardes et diagnostics de déploiement restent locaux.

Les clés fournisseur sont chiffrées en base. Sauvegarder ensemble la base et le fichier maître, dans un emplacement privé ; ne jamais les joindre à une issue ou une release. Les captures de documentation utilisent uniquement des comptes et données fictifs.

Avant publication, vérifier le snapshot livré avec `scripts/check_publication.py` et un scanner de secrets. La CI contrôle les noms de fichiers et lance Gitleaks sur le contenu du snapshot. Un fichier supprimé du dernier commit peut encore exister dans l’historique ; inspecter également les commits destinés à être poussés.

Si une clé a été divulguée, la révoquer auprès du fournisseur et la remplacer. Une suppression de fichier ne révoque pas une clé. Ne pas publier de secret, cookie ou donnée personnelle dans une issue ; contacter le mainteneur en privé pour convenir d’un canal de signalement.
