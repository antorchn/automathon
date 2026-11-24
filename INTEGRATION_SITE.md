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
   « L'adresse à communiquer »).
2. **Arène** : `./scripts/configure_public.sh` sur la VM (INSTALL.md, section 11). La VM accepte alors le port 8000,
   et seulement depuis cette adresse.
3. **Site** : ajouter la règle du reverse proxy (section suivante), recharger, ajouter un lien dans le menu.
4. **Les deux** : les vérifications de la dernière section.

## Ce que le reverse proxy du site doit faire

- Transmettre tout ce qui commence par `/automathon-rlcs/` à `http://<IP-VM-ARENE>:8000`, **sans retirer le
  préfixe** : l'arène attend les chemins complets (`/automathon-rlcs/...`) et construit tous ses liens avec.
- Rediriger `/automathon-rlcs` (sans barre finale) vers `/automathon-rlcs/` (301).
- Garder l'en-tête `Host` d'origine et transmettre `X-Forwarded-For` (l'adresse du visiteur, ajoutée à droite de la
  liste) et `X-Forwarded-Proto: https`. L'arène s'en sert pour ses garde-fous par adresse IP ; elle ne croit que les
  proxys de sa liste de confiance et lit la liste par la droite, donc un visiteur ne peut pas usurper une adresse.
- Accepter des corps de requête jusqu'à 60 Mo : les participants envoient leur agent en zip, 50 Mo au plus.
- Laisser au moins 120 s de délai de réponse.
- Ne rien mettre en cache, et ne modifier ni `Content-Encoding` ni les cookies : le jeu WebGL est servi
  pré-compressé (gzip) par l'arène, et ses cookies de session sont limités au chemin `/automathon-rlcs`.
- Pas de WebSocket, et aucune authentification à ajouter : l'arène gère elle-même la connexion par GitHub.

Entre le site et la VM, le trafic passe en HTTP sur le réseau interne ; HTTPS s'arrête au reverse proxy du site.

### nginx

```nginx
location = /automathon-rlcs { return 301 /automathon-rlcs/; }

location /automathon-rlcs/ {
    proxy_pass http://<IP-VM-ARENE>:8000;     # sans « / » final : le chemin est transmis tel quel
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    client_max_body_size 60m;
    proxy_read_timeout 120s;
    proxy_buffering off;
}
```

### Caddy

```
automatants.cs-campus.fr {
    redir /automathon-rlcs /automathon-rlcs/ 301
    handle /automathon-rlcs/* {
        request_body {
            max_size 60MB
        }
        reverse_proxy <IP-VM-ARENE>:8000
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
    ProxyPass http://<IP-VM-ARENE>:8000/automathon-rlcs/
    ProxyPassReverse http://<IP-VM-ARENE>:8000/automathon-rlcs/
    ProxyPreserveHost On
    RequestHeader set X-Forwarded-Proto "https"
    LimitRequestBody 62914560
    ProxyTimeout 120
</Location>
```

Apache ajoute `X-Forwarded-For` de lui-même.

## L'adresse à communiquer

Le pare-feu de la VM de l'arène n'ouvre le port 8000 qu'à l'adresse du reverse proxy du site. Sur la machine du
reverse proxy :

```bash
ip route get <IP-VM-ARENE>
```

L'adresse qui suit `src` est celle à communiquer. Si une traduction d'adresses (NAT) sépare les deux machines, c'est
l'adresse vue par la VM de l'arène qui compte.

## Vérifications

Une fois la VM configurée (étape 2) :

```bash
# Depuis la machine du reverse proxy : la VM répond
curl -sS http://<IP-VM-ARENE>:8000/automathon-rlcs/health
# -> {"status":"ok","game_version":"1.0.0"}
```

Une fois la règle en place (étape 3), depuis n'importe où :

```bash
curl -sS https://automatants.cs-campus.fr/automathon-rlcs/health      # même réponse
curl -sI https://automatants.cs-campus.fr/automathon-rlcs             # 301 vers /automathon-rlcs/
curl -sI https://automatants.cs-campus.fr/automathon-rlcs/webgl/index.html   # 200
```

Dans un navigateur : `https://automatants.cs-campus.fr/automathon-rlcs/` affiche le classement avec son style, et
« Connexion GitHub » ramène sur l'arène, connecté.

## À ne pas faire

- Retirer le préfixe du chemin (sous nginx, `proxy_pass` avec une barre finale ; sous Caddy, `handle_path` ou
  `uri strip_prefix`) : toutes les pages de l'arène renverraient 404.
- Relayer un autre port de la VM (8001 est local à la VM, 22 est SSH) ou ouvrir la VM directement sur Internet.
- Mettre en cache les pages ou l'API, ou ajouter une authentification devant l'arène.
