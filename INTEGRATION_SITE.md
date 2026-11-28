# Intégrer l'arène Automathon au site de l'association

But : servir l'arène sous un sous-chemin du site de l'association, par exemple
`https://automatants.cs-campus.fr/automathon-rlcs/`. L'arène tourne sur sa propre VM (Debian 13, Docker). Le site de
l'association ne fait que relayer les requêtes : HTTPS, le domaine et le menu restent chez lui, et rien d'autre n'est
à installer de son côté.

Ce document est destiné à qui gère le reverse proxy du site. Les valeurs propres à l'installation (adresse de la VM de
l'arène, sous-chemin) sont données par la personne qui gère l'arène ; elles apparaissent ici entre chevrons :
`<IP-VM-ARENE>`, `/automathon-rlcs`.

## Ordre des opérations

1. **Site** : communiquer l'adresse IP sous laquelle le reverse proxy du site joint la VM de l'arène (section
   « L'adresse à communiquer »), et vérifier qu'il la joint (section « Vérifications », premier test).
2. **Arène** : `./scripts/configure_public.sh` sur la VM (INSTALL.md, section 11). L'arène passe en connexion GitHub
   et la VM accepte le port 80, et seulement depuis cette adresse.
3. **Site** : ajouter la règle du reverse proxy (section suivante), recharger, ajouter un lien dans le menu.
   **Pas avant l'étape 2** : tant que l'arène est en mode test, sa connexion ne vérifie pas l'identité des visiteurs.
4. **Les deux** : les vérifications de la dernière section.

## Ce que le reverse proxy du site doit faire

- Transmettre tout ce qui commence par `/automathon-rlcs/` à `http://<IP-VM-ARENE>` (port 80), **sans retirer le
  préfixe** : l'arène attend les chemins complets (`/automathon-rlcs/...`) et construit tous ses liens avec.
- Faire passer cette règle avant toutes les autres du site : l'application du site, ses fichiers statiques
  (`.js`, `.css`, images, polices) et sa page de repli ne doivent jamais répondre sous `/automathon-rlcs/`. L'arène
  sert elle-même ses feuilles de style, ses polices et le jeu WebGL sous ce préfixe.
- Rediriger `/automathon-rlcs` (sans barre finale) vers `/automathon-rlcs/` (301).
- Garder l'en-tête `Host` d'origine et transmettre `X-Forwarded-For` (l'adresse du visiteur, ajoutée à droite de la
  liste) et `X-Forwarded-Proto: https`. L'arène s'en sert pour ses garde-fous par adresse IP (un envoi par adresse,
  créations de comptes limitées) ; elle ne croit que les proxys de sa liste de confiance et lit la liste par la
  droite, donc un visiteur ne peut pas usurper une adresse. Sans ces en-têtes, tous les visiteurs auraient l'adresse
  du proxy et se bloqueraient entre eux.
- Accepter des corps de requête jusqu'à 60 Mo : les participants envoient leur agent en zip, 50 Mo au plus.
- Laisser au moins 120 s de délai de réponse.
- Ne rien mettre en cache, et ne modifier ni `Content-Encoding` ni les cookies : le jeu WebGL est servi
  pré-compressé (gzip) par l'arène, et ses cookies de session sont limités au chemin `/automathon-rlcs`.
- Pas de WebSocket, et aucune authentification à ajouter : l'arène gère elle-même la connexion par GitHub.

Entre le site et la VM, le trafic passe en HTTP sur le réseau interne ; HTTPS s'arrête au reverse proxy du site.

### nginx

Dans le bloc `server` qui sert `automatants.cs-campus.fr` en HTTPS :

```nginx
location = /automathon-rlcs { return 301 /automathon-rlcs/; }

# ^~ : cette règle l'emporte sur les « location ~ » du site (fichiers statiques, cache), qui sinon répondraient
# à la place de l'arène pour ses .css, .js, .woff2 et le jeu WebGL.
location ^~ /automathon-rlcs/ {
    proxy_pass http://<IP-VM-ARENE>;          # sans chemin ni « / » final : l'URL est transmise telle quelle
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    client_max_body_size 60m;
    proxy_read_timeout 120s;
    proxy_buffering off;
}
```

Puis `sudo nginx -t && sudo systemctl reload nginx` (ou l'équivalent si nginx tourne dans un conteneur). Si le site
passe par une application (Node, par exemple) derrière nginx, rien n'est à changer dans l'application : nginx répond
pour `/automathon-rlcs/` avant elle.

### Caddy

```
automatants.cs-campus.fr {
    redir /automathon-rlcs /automathon-rlcs/ 301
    handle /automathon-rlcs/* {
        request_body {
            max_size 60MB
        }
        reverse_proxy <IP-VM-ARENE>:80
    }
    # ... reste du site
}
```

`handle` (et non `handle_path`) garde le préfixe ; Caddy transmet `X-Forwarded-For` et `X-Forwarded-Proto`
lui-même.

### Apache (mod_proxy, mod_headers)

```apache
RedirectMatch 301 ^/automathon-rlcs$ /automathon-rlcs/
<Location /automathon-rlcs/>
    ProxyPass http://<IP-VM-ARENE>/automathon-rlcs/
    ProxyPassReverse http://<IP-VM-ARENE>/automathon-rlcs/
    ProxyPreserveHost On
    RequestHeader set X-Forwarded-Proto "https"
    LimitRequestBody 62914560
    ProxyTimeout 120
</Location>
```

Apache ajoute `X-Forwarded-For` de lui-même.

## L'adresse à communiquer

Le pare-feu de la VM de l'arène n'ouvre le port 80 qu'à l'adresse du reverse proxy du site. Sur la machine du
reverse proxy :

```bash
ip route get <IP-VM-ARENE>
```

L'adresse qui suit `src` est celle à communiquer. Si une traduction d'adresses (NAT) sépare les deux machines, c'est
l'adresse vue par la VM de l'arène qui compte. Si nginx tourne dans un conteneur Docker, c'est l'adresse de la
machine hôte.

## Vérifications

Depuis la machine du reverse proxy (dès que la VM de l'arène accepte son adresse, avant même l'étape 2) :

```bash
curl -sS -m 10 http://<IP-VM-ARENE>/automathon-rlcs/health
# -> {"status":"ok","game_version":"1.0.0"}
```

Si la commande attend puis échoue (délai dépassé), un pare-feu bloque entre les deux machines, ou l'adresse
communiquée n'est pas la bonne : revoir « L'adresse à communiquer ».

Une fois la règle en place (étape 3), depuis n'importe où :

```bash
curl -sS https://automatants.cs-campus.fr/automathon-rlcs/health                      # même réponse, en JSON
curl -sI https://automatants.cs-campus.fr/automathon-rlcs                             # 301 vers /automathon-rlcs/
curl -sI https://automatants.cs-campus.fr/automathon-rlcs/static/style.css            # 200, text/css
curl -sI https://automatants.cs-campus.fr/automathon-rlcs/webgl/index.html            # 200
```

Si `/health` renvoie une page HTML du site plutôt que du JSON, c'est le site qui répond à la place de l'arène : la
règle n'est pas prise en compte, ou une autre la précède.

Dans un navigateur : `https://automatants.cs-campus.fr/automathon-rlcs/` affiche le classement avec son style, et
« Connexion GitHub » ramène sur l'arène, connecté.

## À ne pas faire

- Retirer le préfixe du chemin (sous nginx, `proxy_pass` avec une barre ou un chemin final ; sous Caddy,
  `handle_path` ou `uri strip_prefix`) : toutes les pages de l'arène renverraient 404.
- Relayer un autre port de la VM (8001 est local à la VM, 22 est SSH) ou ouvrir la VM directement sur Internet.
- Mettre en cache les pages ou l'API, ou ajouter une authentification devant l'arène.
