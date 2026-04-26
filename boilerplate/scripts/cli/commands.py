# -*- coding: utf-8 -*-
"""
CLI Click — Couche 2 : commandes scriptables.

Point d'entrée : le groupe `cli` est importé par mcp_cli.py.
Chaque commande appelle un outil MCP via MCPClient puis affiche via display.py.

Usage :
    python scripts/mcp_cli.py health
    python scripts/mcp_cli.py about
    python scripts/mcp_cli.py shell
"""

import asyncio
import click
from . import BASE_URL, TOKEN
from .client import MCPClient
from .display import (
    console, show_error, show_success,
    show_health_result, show_about_result, show_json,
    show_token_create_result, show_token_list_result, show_token_revoke_result,
    show_whoami_result,
)


@click.group()
@click.option(
    "--url", "-u",
    envvar=["MCP_URL"],
    default=BASE_URL,
    help="URL du serveur MCP",
)
@click.option(
    "--token", "-t",
    envvar=["MCP_TOKEN"],
    default=TOKEN,
    help="Token d'authentification",
)
@click.pass_context
def cli(ctx, url, token):
    """🔧 CLI pour le service MCP."""
    ctx.ensure_object(dict)
    ctx.obj["url"] = url
    ctx.obj["token"] = token


# =============================================================================
# Commandes système (incluses dans le boilerplate)
# =============================================================================

@cli.command("health")
@click.option("--json", "-j", "output_json", is_flag=True, help="Sortie JSON brute")
@click.pass_context
def health_cmd(ctx, output_json):
    """❤️  Vérifier l'état de santé du service."""
    async def _run():
        try:
            client = MCPClient(ctx.obj["url"], ctx.obj["token"])
            result = await client.call_tool("system_health", {})
            if output_json:
                show_json(result)
            elif result.get("status") in ("ok", "healthy"):
                show_health_result(result)
            else:
                show_error(result.get("message", "Service indisponible"))
        except Exception as e:
            show_error(f"Connexion impossible: {e}")
    asyncio.run(_run())


@cli.command("about")
@click.option("--json", "-j", "output_json", is_flag=True, help="Sortie JSON brute")
@click.pass_context
def about_cmd(ctx, output_json):
    """ℹ️  Informations sur le service MCP."""
    async def _run():
        try:
            client = MCPClient(ctx.obj["url"], ctx.obj["token"])
            result = await client.call_tool("system_about", {})
            if output_json:
                show_json(result)
            elif result.get("status") == "ok":
                show_about_result(result)
            else:
                show_error(result.get("message", "Erreur"))
        except Exception as e:
            show_error(f"Connexion impossible: {e}")
    asyncio.run(_run())


@cli.command("shell")
@click.pass_context
def shell_cmd(ctx):
    """🐚 Lancer le shell interactif."""
    from .shell import run_shell
    asyncio.run(run_shell(ctx.obj["url"], ctx.obj["token"]))


@cli.command("whoami")
@click.option("--json", "-j", "output_json", is_flag=True, help="Sortie JSON brute")
@click.pass_context
def whoami_cmd(ctx, output_json):
    """👤 Identité du token courant."""
    async def _run():
        try:
            client = MCPClient(ctx.obj["url"], ctx.obj["token"])
            result = await client.call_tool("system_whoami", {})
            if output_json:
                show_json(result)
            elif result.get("status") == "ok":
                show_whoami_result(result, url=ctx.obj["url"])
            else:
                show_error(result.get("message", "Erreur"))
        except Exception as e:
            show_error(f"Connexion impossible: {e}")
    asyncio.run(_run())


# =============================================================================
# Commandes token (gestion des tokens d'accès)
# =============================================================================

@cli.group("token")
def token_group():
    """🔑 Gestion des tokens d'accès (admin via /admin/api)."""
    pass


@token_group.command("create")
@click.argument("client_name")
@click.option("--permissions", "-p", default="read,write", help="Permissions (ex: read,write,admin)")
@click.option("--email", "-e", default="", help="Email du propriétaire (traçabilité)")
@click.option("--expires", "-d", default=90, type=int, help="Expiration en jours (0 = jamais)")
@click.option("--vaults", "-s", default="", help="Vaults/ressources autorisés (virgule, vide = aucune restriction explicite)")
@click.option("--json", "-j", "output_json", is_flag=True, help="Sortie JSON brute")
@click.pass_context
def token_create_cmd(ctx, client_name, permissions, email, expires, vaults, output_json):
    """Créer un nouveau token via l'API admin REST."""
    async def _run():
        try:
            client = MCPClient(ctx.obj["url"], ctx.obj["token"])
            perms = [p.strip() for p in permissions.split(",") if p.strip()]
            vault_ids = [v.strip() for v in vaults.split(",") if v.strip()] if vaults else []
            result = await client.call_admin_api("POST", "/tokens", {
                "client_name": client_name,
                "permissions": perms,
                "allowed_resources": vault_ids,
                "email": email,
                "expires_in_days": expires,
            })
            if output_json:
                show_json(result)
            elif result.get("status") in ("ok", "created"):
                show_token_create_result(result)
            else:
                show_error(result.get("message", "Erreur"))
        except Exception as e:
            show_error(f"Erreur: {e}")
    asyncio.run(_run())


@token_group.command("list")
@click.option("--json", "-j", "output_json", is_flag=True, help="Sortie JSON brute")
@click.pass_context
def token_list_cmd(ctx, output_json):
    """Lister les tokens existants via l'API admin REST."""
    async def _run():
        try:
            client = MCPClient(ctx.obj["url"], ctx.obj["token"])
            result = await client.call_admin_api("GET", "/tokens")
            if output_json:
                show_json(result)
            elif result.get("status") == "ok":
                show_token_list_result(result)
            else:
                show_error(result.get("message", "Erreur"))
        except Exception as e:
            show_error(f"Erreur: {e}")
    asyncio.run(_run())


@token_group.command("update")
@click.argument("hash_prefix")
@click.option("--permissions", "-p", default="", help="Nouvelles permissions (virgule)")
@click.option("--vaults", "-s", default="", help="Vaults/ressources autorisés (virgule, vide = inchangé)")
@click.option("--json", "-j", "output_json", is_flag=True, help="Sortie JSON brute")
@click.pass_context
def token_update_cmd(ctx, hash_prefix, permissions, vaults, output_json):
    """Modifier un token (permissions, allowed_resources) via l'API admin REST."""
    async def _run():
        try:
            client = MCPClient(ctx.obj["url"], ctx.obj["token"])
            payload = {}
            if permissions:
                payload["permissions"] = [p.strip() for p in permissions.split(",") if p.strip()]
            if vaults:
                payload["allowed_resources"] = [v.strip() for v in vaults.split(",") if v.strip()]
            result = await client.call_admin_api("PUT", f"/tokens/{hash_prefix}", payload)
            if output_json:
                show_json(result)
            elif result.get("status") in ("ok", "updated"):
                show_success(result.get("message", "Token mis à jour"))
            else:
                show_error(result.get("message", "Erreur"))
        except Exception as e:
            show_error(f"Erreur: {e}")
    asyncio.run(_run())


@token_group.command("revoke")
@click.argument("hash_prefix")
@click.option("--json", "-j", "output_json", is_flag=True, help="Sortie JSON brute")
@click.pass_context
def token_revoke_cmd(ctx, hash_prefix, output_json):
    """Révoquer un token par préfixe de hash via l'API admin REST."""
    async def _run():
        try:
            client = MCPClient(ctx.obj["url"], ctx.obj["token"])
            result = await client.call_admin_api("DELETE", f"/tokens/{hash_prefix}")
            if output_json:
                show_json(result)
            elif result.get("status") == "ok":
                show_token_revoke_result(result)
            else:
                show_error(result.get("message", "Erreur"))
        except Exception as e:
            show_error(f"Erreur: {e}")
    asyncio.run(_run())


# =============================================================================
# Ajouter vos commandes métier ici
# =============================================================================
# Exemple :
#
# @cli.command("mon-outil")
# @click.argument("resource_id")
# @click.option("--param", "-p", required=True)
# @click.pass_context
# def mon_outil_cmd(ctx, resource_id, param):
#     """🔧 Description courte."""
#     async def _run():
#         client = MCPClient(ctx.obj["url"], ctx.obj["token"])
#         result = await client.call_tool("mon_outil", {
#             "resource_id": resource_id, "param": param
#         })
#         if result.get("status") == "ok":
#             show_mon_outil_result(result)
#         else:
#             show_error(result.get("message", "Erreur"))
#     asyncio.run(_run())
