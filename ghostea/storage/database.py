import json
import urllib.error
import urllib.parse
import urllib.request


class SupabaseREST:
    """
    Small dependency-free Supabase/PostgREST client.

    The server-side key must be supplied through SUPABASE_KEY and must
    never be committed to GitHub. This is intentionally implemented with
    urllib so Android/Termux does not need another database package.
    """

    def __init__(self, url: str, key: str):
        self.base = url.rstrip("/") + "/rest/v1"
        self.key = key

    def _request(self, method, table, payload=None, query=None, prefer=None):
        url = f"{self.base}/{table}"
        if query:
            url += "?" + urllib.parse.urlencode(query, doseq=True)

        body = None
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")

        headers = {
            "apikey": self.key,
            "Authorization": f"Bearer {self.key}",
            "Content-Type": "application/json",
        }
        if prefer:
            headers["Prefer"] = prefer

        req = urllib.request.Request(
            url,
            data=body,
            headers=headers,
            method=method,
        )

        try:
            with urllib.request.urlopen(req, timeout=15) as response:
                raw = response.read().decode("utf-8")
                if not raw:
                    return []
                return json.loads(raw)
        except urllib.error.HTTPError as error:
            details = error.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"Supabase HTTP {error.code}: {details}"
            ) from error
        except urllib.error.URLError as error:
            raise RuntimeError(f"Supabase connection failed: {error}") from error

    def select(self, table, query):
        return self._request("GET", table, query=query)

    def insert(self, table, payload, return_rows=False):
        prefer = "return=representation" if return_rows else "return=minimal"
        return self._request("POST", table, payload, prefer=prefer)

    def upsert(self, table, payload):
        return self._request(
            "POST",
            table,
            payload,
            prefer="resolution=merge-duplicates,return=representation",
        )

    def update(self, table, payload, query):
        return self._request("PATCH", table, payload, query=query, prefer="return=representation")

    def delete(self, table, query):
        return self._request("DELETE", table, query=query)

    def count(self, table, query=None):
        """Return an exact PostgREST row count without downloading the table."""
        query = dict(query or {})
        query.setdefault("select", "id")
        url = f"{self.base}/{table}?" + urllib.parse.urlencode(query, doseq=True)
        headers = {
            "apikey": self.key,
            "Authorization": f"Bearer {self.key}",
            "Content-Type": "application/json",
            "Prefer": "count=exact",
            "Range": "0-0",
        }
        req = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=15) as response:
                content_range = response.headers.get("Content-Range", "")
                total = content_range.rsplit("/", 1)[-1] if "/" in content_range else "0"
                return int(total) if total != "*" else 0
        except urllib.error.HTTPError as error:
            details = error.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Supabase HTTP {error.code}: {details}") from error
        except urllib.error.URLError as error:
            raise RuntimeError(f"Supabase connection failed: {error}") from error
