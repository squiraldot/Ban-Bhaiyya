import asyncio
import re
import hashlib
import hmac
import os
import secrets
import time
from datetime import datetime, timezone


ROLES = ("super_admin", "admin", "moderator", "viewer")

ROLE_PERMISSIONS = {
    "super_admin": {"read", "moderate", "settings", "manage_admins"},
    "admin": {"read", "moderate", "settings"},
    "moderator": {"read", "moderate"},
    "viewer": {"read"},
}


def _hash_password(password, salt=None):
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=2**14,
        r=8,
        p=1,
    )
    return f"scrypt$16384$8$1${salt.hex()}${digest.hex()}"


def _verify_password(password, encoded):
    try:
        algorithm, n, r, p, salt_hex, digest_hex = encoded.split("$")
        if algorithm != "scrypt":
            return False
        expected = bytes.fromhex(digest_hex)
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=bytes.fromhex(salt_hex),
            n=int(n),
            r=int(r),
            p=int(p),
        )
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


class AdminService:
    """Database-backed dashboard admins with role-based authorization."""

    def __init__(self, store):
        self.store = store
        self._auth_cache = {}
        self._auth_cache_ttl = 30.0
        self._mutation_lock = asyncio.Lock()

    async def _rows(self, query):
        return await self.store._call(
            self.store.db.select, "ghostea_admins", query
        )

    async def bootstrap(self):
        username = os.getenv("GHOSTEA_SUPERADMIN_USERNAME", "superadmin").strip().lower()
        password = os.getenv("GHOSTEA_ADMIN_PASSWORD", "").strip()
        if not username or not password:
            raise RuntimeError("Super admin bootstrap credentials are not configured.")

        rows = await self._rows({
            "username": f"eq.{username}",
            "limit": "1",
        })
        if not rows:
            try:
                await self.store._call(
                    self.store.db.insert,
                    "ghostea_admins",
                    {
                        "username": username,
                        "display_name": "Super Admin",
                        "password_hash": _hash_password(password),
                        "role": "super_admin",
                        "enabled": True,
                    },
                )
            except RuntimeError as error:
                # Another request/process may have won the bootstrap race.
                # Re-read the account and continue if it now exists.
                rows = await self._rows({
                    "username": f"eq.{username}",
                    "limit": "1",
                })
                if not rows:
                    raise error

    async def authenticate(self, username, password):
        username = str(username or "").strip().lower()
        if not username or not isinstance(password, str):
            return None

        await self.bootstrap()
        rows = await self._rows({
            "username": f"eq.{username}",
            "limit": "1",
        })
        if not rows:
            return None
        admin = rows[0]
        if not admin.get("enabled", True):
            return None
        if not _verify_password(password, admin.get("password_hash", "")):
            return None

        await self.store._call(
            self.store.db.update,
            "ghostea_admins",
            {"last_login_at": datetime.now(timezone.utc).isoformat()},
            {"id": f"eq.{admin['id']}"},
        )
        return self.public(admin)

    @staticmethod
    def public(admin):
        return {
            "id": int(admin["id"]),
            "username": admin["username"],
            "display_name": admin.get("display_name") or admin["username"],
            "role": admin["role"],
            "enabled": bool(admin.get("enabled", True)),
            "created_at": admin.get("created_at"),
            "last_login_at": admin.get("last_login_at"),
        }

    async def authorize(self, admin_id, claimed_role):
        """Validate the short-lived Vercel session against current DB state."""
        try:
            admin_id = int(admin_id)
        except (TypeError, ValueError):
            return None
        now = time.monotonic()
        cached = self._auth_cache.get(admin_id)
        if cached and cached[0] > now:
            admin = cached[1]
        else:
            rows = await self._rows({
                "id": f"eq.{admin_id}",
                "limit": "1",
            })
            if not rows:
                return None
            admin = rows[0]
            self._auth_cache[admin_id] = (now + self._auth_cache_ttl, admin)
        if not admin.get("enabled", True):
            return None
        if str(admin.get("role")) != str(claimed_role):
            return None
        return self.public(admin)

    @staticmethod
    def allowed(role, permission):
        return permission in ROLE_PERMISSIONS.get(role, set())

    async def list_admins(self):
        rows = await self._rows({
            "select": "id,username,display_name,role,enabled,created_at,last_login_at",
            "order": "created_at.asc",
            "limit": "500",
        })
        return [self.public(x) for x in rows]

    async def create(self, username, password, role, display_name=""):
        async with self._mutation_lock:
            return await self._create_locked(username, password, role, display_name)

    async def _create_locked(self, username, password, role, display_name=""):
        username = str(username or "").strip().lower()
        role = str(role or "").strip()
        display_name = str(display_name or "").strip()[:100]
        if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{2,31}", username):
            raise ValueError("invalid_username")
        if not isinstance(password, str) or not 10 <= len(password) <= 256:
            raise ValueError("invalid_password")
        if role not in ROLES:
            raise ValueError("invalid_role")

        existing = await self._rows({
            "username": f"eq.{username}",
            "limit": "1",
        })
        if existing:
            raise ValueError("username_exists")

        rows = await self.store._call(
            self.store.db.insert,
            "ghostea_admins",
            {
                "username": username,
                "display_name": display_name or username,
                "password_hash": _hash_password(password),
                "role": role,
                "enabled": True,
            },
            True,
        )
        return self.public(rows[0]) if rows else None

    async def update(self, admin_id, changes):
        async with self._mutation_lock:
            return await self._update_locked(admin_id, changes)

    async def _update_locked(self, admin_id, changes):
        admin_id = int(admin_id)
        allowed = {}
        if "display_name" in changes:
            allowed["display_name"] = str(changes["display_name"] or "").strip()[:100]
        if "role" in changes:
            role = str(changes["role"] or "").strip()
            if role not in ROLES:
                raise ValueError("invalid_role")
            allowed["role"] = role
        if "enabled" in changes:
            if not isinstance(changes["enabled"], bool):
                raise ValueError("invalid_enabled")
            allowed["enabled"] = changes["enabled"]
        if "password" in changes:
            password = changes["password"]
            if not isinstance(password, str) or not 10 <= len(password) <= 256:
                raise ValueError("invalid_password")
            allowed["password_hash"] = _hash_password(password)

        if not allowed:
            raise ValueError("no_changes")
        allowed["updated_at"] = datetime.now(timezone.utc).isoformat()

        current = await self._rows({"id": f"eq.{admin_id}", "limit": "1"})
        if not current:
            raise ValueError("admin_not_found")

        # Never allow the last enabled super admin to be removed/demoted.
        if current[0].get("role") == "super_admin":
            if allowed.get("role") and allowed["role"] != "super_admin":
                count = await self._rows({
                    "role": "eq.super_admin",
                    "enabled": "eq.true",
                    "select": "id",
                    "limit": "2",
                })
                if len(count) < 2:
                    raise ValueError("last_super_admin")
            if allowed.get("enabled") is False:
                count = await self._rows({
                    "role": "eq.super_admin",
                    "enabled": "eq.true",
                    "select": "id",
                    "limit": "2",
                })
                if len(count) < 2:
                    raise ValueError("last_super_admin")

        rows = await self.store._call(
            self.store.db.update,
            "ghostea_admins",
            allowed,
            {"id": f"eq.{admin_id}"},
        )
        self._auth_cache.pop(admin_id, None)
        return self.public(rows[0]) if rows else None

    async def delete(self, admin_id):
        async with self._mutation_lock:
            return await self._delete_locked(admin_id)

    async def _delete_locked(self, admin_id):
        admin_id = int(admin_id)
        current = await self._rows({"id": f"eq.{admin_id}", "limit": "1"})
        if not current:
            raise ValueError("admin_not_found")
        if current[0].get("role") == "super_admin":
            count = await self._rows({
                "role": "eq.super_admin",
                "enabled": "eq.true",
                "select": "id",
                "limit": "2",
            })
            if len(count) < 2:
                raise ValueError("last_super_admin")
        await self.store._call(
            self.store.db.delete,
            "ghostea_admins",
            {"id": f"eq.{admin_id}"},
        )
        self._auth_cache.pop(admin_id, None)
        return {"ok": True}
