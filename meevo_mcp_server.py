"""
Meevo MCP Server
================
Exposes Meevo API endpoints as MCP tools so Conduit's AI agent
can look up clients, appointments, and services in real-time.

Deploy this to Render.com (see render.yaml), then paste the
resulting URL into Conduit > Settings > Connections > Add MCP Server.

Local dev:
  pip install -r requirements.txt
  python meevo_mcp_server.py
"""

import os
import time
import requests
from datetime import date, timedelta
from mcp.server.fastmcp import FastMCP

# ─── Config (set as environment variables on Render) ───────────────────────────
APP_ID       = os.environ.get("MEEVO_APP_ID",       "ac5673cc-9d40-4483-85b6-232b109d027e")
APP_SECRET   = os.environ.get("MEEVO_APP_SECRET",   "2c835721-4710-4034-8811-7301f8fed2b6")
AUTH_URL     = os.environ.get("MEEVO_AUTH_URL",     "https://d18devmarketplace.meevodev.com/oauth2/token")
BASE_URL     = os.environ.get("MEEVO_BASE_URL",     "https://d18devpub.meevodev.com")
TENANT_ID    = os.environ.get("MEEVO_TENANT_ID",    "4")
LOCATION_ID  = os.environ.get("MEEVO_LOCATION_ID",  "3")
SERVER_PORT  = int(os.environ.get("PORT", "8000"))

# ─── Auth token cache ──────────────────────────────────────────────────────────
_token: str | None = None
_token_expiry: float = 0


def get_token() -> str:
    global _token, _token_expiry
    if _token and time.time() < _token_expiry:
        return _token
    r = requests.post(
        AUTH_URL,
        data={"client_id": APP_ID, "client_secret": APP_SECRET, "grant_type": "client_credentials"},
        headers={"Accept": "application/json"},
        timeout=10,
    )
    r.raise_for_status()
    d = r.json()
    _token = d["access_token"]
    _token_expiry = time.time() + d.get("expires_in", 3600) - 60
    return _token


def meevo_get(path: str, params: dict | None = None) -> dict:
    base = {"tenantId": TENANT_ID, "locationId": LOCATION_ID}
    if params:
        base.update(params)
    r = requests.get(
        f"{BASE_URL}{path}",
        params=base,
        headers={"Authorization": f"Bearer {get_token()}", "Accept": "application/json"},
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


def _items(data: dict) -> list:
    """Extract the item list regardless of which key Meevo uses."""
    for key in ("Clients", "Appointments", "Services", "Employees", "Data", "Items"):
        if key in data:
            return data[key]
    return []


# ─── MCP server ───────────────────────────────────────────────────────────────
mcp = FastMCP("Meevo", streamable_http_path="/sse")


@mcp.tool()
def lookup_client(phone: str = "", email: str = "") -> dict:
    """
    Look up a Meevo client by phone number or email address.
    Returns their profile including name, contact info, and notes.
    Provide at least one of: phone or email.
    """
    if not phone and not email:
        return {"error": "Provide a phone number or email."}

    clean_phone = phone.replace(" ", "").replace("-", "").replace("(", "").replace(")", "").replace("+1", "")

    data = meevo_get("/publicapi/v1/clients", {"PhoneNumber": clean_phone} if phone else {"Email": email})
    items = _items(data)

    if not items:
        return {"found": False, "message": f"No client found for {phone or email}."}

    c = items[0]
    phones = c.get("PhoneNumbers") or []
    return {
        "found": True,
        "client_id": c.get("ClientId") or c.get("Id"),
        "name": f"{c.get('FirstName', '')} {c.get('LastName', '')}".strip(),
        "email": c.get("Email") or c.get("EmailAddress", ""),
        "phones": [p.get("Number") or p.get("PhoneNumber", "") for p in phones],
        "birth_date": c.get("BirthDate", ""),
        "gender": c.get("Gender", ""),
        "member_since": c.get("CreatedDate", ""),
        "notes": c.get("Notes", ""),
        "is_active": c.get("IsActive", True),
    }


@mcp.tool()
def get_client_appointments(client_id: str, days_back: int = 90, days_ahead: int = 60) -> dict:
    """
    Get upcoming and recent appointments for a Meevo client.
    Use lookup_client first to get the client_id.
    Returns upcoming appointments (next 60 days) and recent visits (last 90 days).
    """
    start = (date.today() - timedelta(days=days_back)).isoformat()
    end = (date.today() + timedelta(days=days_ahead)).isoformat()
    data = meevo_get("/publicapi/v1/appointments", {
        "ClientId": client_id,
        "StartDate": start,
        "EndDate": end,
        "ItemsPerPage": 25,
    })
    appts = _items(data)

    today = date.today().isoformat()
    upcoming, past = [], []
    for a in appts:
        dt = a.get("StartDateTime") or a.get("Date", "")
        entry = {
            "date": dt,
            "service": a.get("ServiceName") or a.get("Service", ""),
            "staff": a.get("EmployeeName") or a.get("Employee", ""),
            "status": a.get("StatusDescription") or a.get("Status", ""),
            "duration_minutes": a.get("Duration") or a.get("DurationMinutes", ""),
        }
        if dt >= today:
            upcoming.append(entry)
        else:
            past.append(entry)

    return {
        "client_id": client_id,
        "upcoming_appointments": upcoming[:10],
        "recent_visits": past[-10:],
        "total_past_visits": len(past),
    }


@mcp.tool()
def check_availability(service_id: str, check_date: str = "") -> dict:
    """
    Check available appointment times for a service on a given date.
    check_date format: YYYY-MM-DD (defaults to today if not provided).
    Use list_services to get service IDs.
    """
    d = check_date or date.today().isoformat()
    data = meevo_get("/publicapi/v1/appointments/availabletimes", {
        "ServiceId": service_id,
        "Date": d,
    })
    slots = data.get("AvailableTimes") or data.get("Times") or _items(data)
    return {
        "service_id": service_id,
        "date": d,
        "available_times": slots[:20],
        "total_slots": len(slots),
    }


@mcp.tool()
def list_services(page: int = 1) -> dict:
    """
    List all services offered at the spa.
    Returns service names, IDs, durations, and prices.
    """
    data = meevo_get("/publicapi/v1/services", {"PageNumber": page, "ItemsPerPage": 50})
    services = _items(data)
    return {
        "services": [
            {
                "id": s.get("ServiceId") or s.get("Id"),
                "name": s.get("ServiceName") or s.get("Name", ""),
                "category": s.get("CategoryName") or s.get("Category", ""),
                "duration_minutes": s.get("Duration") or s.get("DurationMinutes", ""),
                "price": s.get("Price") or s.get("RetailPrice", ""),
                "description": s.get("Description", ""),
            }
            for s in services
        ],
        "total": data.get("TotalItems") or data.get("TotalCount") or len(services),
        "page": page,
    }


@mcp.tool()
def list_staff(page: int = 1) -> dict:
    """
    List all staff/employees at the spa.
    Returns names, IDs, and specialties.
    """
    data = meevo_get("/publicapi/v1/employees", {"PageNumber": page, "ItemsPerPage": 50})
    staff = _items(data)
    return {
        "staff": [
            {
                "id": e.get("EmployeeId") or e.get("Id"),
                "name": f"{e.get('FirstName', '')} {e.get('LastName', '')}".strip(),
                "title": e.get("Title") or e.get("JobTitle", ""),
                "is_active": e.get("IsActive", True),
            }
            for e in staff
        ],
        "total": data.get("TotalItems") or data.get("TotalCount") or len(staff),
    }


@mcp.tool()
def get_recent_changes(hours_back: int = 24) -> dict:
    """
    Get all data changes in Meevo from the last N hours.
    Useful for syncing updates — returns changed clients, appointments, etc.
    Default is last 24 hours.
    """
    since_ts = int(time.time()) - (hours_back * 3600)
    data = meevo_get("/publicapi/v1/changes", {"LastChangeTimestamp": since_ts})
    return data


# ─── Entry point ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn

    _PORT = int(os.environ.get("PORT", 8000))
    _inner = mcp.streamable_http_app()

    # Render forwards requests with Host: meevo-mcp.onrender.com, but the MCP
    # SDK's transport_security.py rejects any host that isn't localhost/IP
    # (DNS-rebinding protection).  Rewrite the header before it gets there.
    async def app(scope, receive, send):
        if scope["type"] == "http":
            scope["headers"] = [
                (b"host", b"localhost") if k == b"host" else (k, v)
                for k, v in scope["headers"]
            ]
        await _inner(scope, receive, send)

    uvicorn.run(app, host="0.0.0.0", port=_PORT)
