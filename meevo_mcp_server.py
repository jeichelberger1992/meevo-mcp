"""
Meevo MCP Server
================
Exposes Meevo API endpoints as MCP tools so Conduit's AI agent
can look up clients, appointments, and services in real-time,
and book, reschedule, or cancel appointments.
"""

import os
import time
import requests
from datetime import date, timedelta
from mcp.server.fastmcp import FastMCP

APP_ID       = os.environ.get("MEEVO_APP_ID",       "ac5673cc-9d40-4483-85b6-232b109d027e")
APP_SECRET   = os.environ.get("MEEVO_APP_SECRET",   "2c835721-4710-4034-8811-7301f8fed2b6")
AUTH_URL     = os.environ.get("MEEVO_AUTH_URL",     "https://d18devmarketplace.meevodev.com/oauth2/token")
BASE_URL     = os.environ.get("MEEVO_BASE_URL",     "https://d18devpub.meevodev.com")
TENANT_ID    = os.environ.get("MEEVO_TENANT_ID",    "4")
LOCATION_ID  = os.environ.get("MEEVO_LOCATION_ID",  "3")

_token = None
_token_expiry = 0.0


def get_token():
    global _token, _token_expiry
    if _token and time.time() < _token_expiry:
        return _token
    r = requests.post(AUTH_URL, data={"client_id": APP_ID, "client_secret": APP_SECRET, "grant_type": "client_credentials"}, headers={"Accept": "application/json"}, timeout=10)
    r.raise_for_status()
    d = r.json()
    _token = d["access_token"]
    _token_expiry = time.time() + d.get("expires_in", 3600) - 60
    return _token


def _auth_headers():
    return {"Authorization": f"Bearer {get_token()}", "Accept": "application/json", "Content-Type": "application/json"}


def meevo_get(path, params=None):
    base = {"tenantId": TENANT_ID, "locationId": LOCATION_ID}
    if params:
        base.update(params)
    r = requests.get(f"{BASE_URL}{path}", params=base, headers=_auth_headers(), timeout=15)
    r.raise_for_status()
    return r.json()


def meevo_post(path, body, params=None):
    base = {"tenantId": TENANT_ID, "locationId": LOCATION_ID}
    if params:
        base.update(params)
    r = requests.post(f"{BASE_URL}{path}", params=base, json=body, headers=_auth_headers(), timeout=15)
    r.raise_for_status()
    return r.json() if r.content else {"success": True}


def meevo_put(path, body, params=None):
    base = {"tenantId": TENANT_ID, "locationId": LOCATION_ID}
    if params:
        base.update(params)
    r = requests.put(f"{BASE_URL}{path}", params=base, json=body, headers=_auth_headers(), timeout=15)
    r.raise_for_status()
    return r.json() if r.content else {"success": True}


def meevo_delete(path, params=None):
    base = {"tenantId": TENANT_ID, "locationId": LOCATION_ID}
    if params:
        base.update(params)
    r = requests.delete(f"{BASE_URL}{path}", params=base, headers=_auth_headers(), timeout=15)
    r.raise_for_status()
    return r.json() if r.content else {"success": True}


def _items(data):
    for key in ("Clients", "Appointments", "Services", "Employees", "Data", "Items", "Results", "Records",
               "clients", "appointments", "services", "employees", "data", "items", "results", "records"):
        if key in data:
            return data[key]
    return []


def _get(obj, *keys, default=""):
    for k in keys:
        v = obj.get(k)
        if v is not None:
            return v
    return default


def _str(v):
    if v is None:
        return ""
    return str(v)


mcp = FastMCP("Meevo", host="0.0.0.0", stateless_http=True)


@mcp.custom_route("/health", methods=["GET"])
async def health_check(request):
    from starlette.responses import PlainTextResponse
    return PlainTextResponse("OK")


@mcp.tool()
def debug_api(path: str) -> dict:
    """Call any Meevo API path and return the raw response."""
    try:
        data = meevo_get(path)
        sample = data
        if isinstance(data, dict) and "data" in data and isinstance(data["data"], list) and data["data"]:
            sample = {"envelope_keys": list(data.keys()), "first_item_keys": list(data["data"][0].keys()), "first_item": data["data"][0], "total_items": len(data["data"])}
        return {"path": path, "type": type(data).__name__, "keys": list(data.keys()) if isinstance(data, dict) else None, "length": len(data) if isinstance(data, (list, dict)) else None, "sample": str(sample)[:3000]}
    except requests.HTTPError as e:
        return {"error": str(e), "status": e.response.status_code if e.response else None, "body": e.response.text[:500] if e.response else ""}


@mcp.tool()
def search_clients(last_name: str = "", first_name: str = "", phone: str = "", email: str = "") -> dict:
    """Search for Meevo clients by name, phone, or email using the filter endpoint."""
    clean_phone = phone.replace(" ", "").replace("-", "").replace("(", "").replace(")", "").replace("+1", "")
    body = {}
    if last_name:
        body["lastName"] = last_name
    if first_name:
        body["firstName"] = first_name
    if clean_phone:
        body["phoneNumber"] = clean_phone
    if email:
        body["emailAddress"] = email
    try:
        last_error = None
        data = None
        for path in ["/publicapi/v1/clients/filter", "/publicapi/v1/clients/filtercriteria", "/publicapi/v1/clients"]:
            try:
                data = meevo_post(path, body)
                break
            except requests.HTTPError as e:
                if e.response is not None and e.response.status_code == 404:
                    last_error = e
                    continue
                raise
        if data is None:
            raise last_error
        clients = data.get("data") or data.get("Data") or _items(data)
        if not clients:
            return {"found": False, "raw_keys": list(data.keys()) if isinstance(data, dict) else str(data)[:300]}
        results = []
        for c in clients[:5]:
            phones = c.get("phoneNumbers") or c.get("PhoneNumbers") or []
            results.append({
                "client_id": _get(c, "id", "clientId", "Id", "ClientId"),
                "name": f"{_get(c, 'firstName', 'FirstName')} {_get(c, 'lastName', 'LastName')}".strip(),
                "email": _get(c, "emailAddress", "email", "Email", "EmailAddress"),
                "phones": [_get(p, "number", "Number", "phoneNumber", "PhoneNumber") for p in phones],
            })
        return {"found": True, "clients": results, "total": len(clients)}
    except requests.HTTPError as e:
        return {"error": str(e), "status": e.response.status_code if e.response else None, "body": e.response.text[:500] if e.response else ""}


@mcp.tool()
def lookup_client(phone: str = "", email: str = "") -> dict:
    """Look up a Meevo client by phone number or email address."""
    if not phone and not email:
        return {"error": "Provide a phone number or email."}
    clean_phone = phone.replace(" ", "").replace("-", "").replace("(", "").replace(")", "").replace("+1", "")
    data = meevo_get("/publicapi/v1/clients", {"PhoneNumber": clean_phone} if phone else {"Email": email})
    items = _items(data)
    if not items:
        return {"found": False, "message": f"No client found for {phone or email}.", "raw_keys": list(data.keys()) if isinstance(data, dict) else str(data)[:200]}
    c = items[0]
    phones = c.get("PhoneNumbers") or c.get("phoneNumbers") or []
    first = _get(c, "FirstName", "firstName")
    last = _get(c, "LastName", "lastName")
    return {
        "found": True,
        "client_id": _get(c, "ClientId", "clientId", "Id", "id"),
        "name": f"{first} {last}".strip(),
        "email": _get(c, "Email", "email", "EmailAddress", "emailAddress"),
        "phones": [_get(p, "Number", "number", "PhoneNumber", "phoneNumber") for p in phones],
        "birth_date": _get(c, "BirthDate", "birthDate"),
        "notes": _get(c, "Notes", "notes"),
        "is_active": c.get("IsActive") if c.get("IsActive") is not None else c.get("isActive", True),
    }


@mcp.tool()
def get_client_appointments(client_id: str, days_back: int = 90, days_ahead: int = 60) -> dict:
    """Get upcoming and recent appointments for a Meevo client."""
    start = (date.today() - timedelta(days=days_back)).isoformat()
    end = (date.today() + timedelta(days=days_ahead)).isoformat()
    data = meevo_get("/publicapi/v1/appointments", {"ClientId": client_id, "StartDate": start, "EndDate": end, "ItemsPerPage": 25})
    appts = _items(data)
    today = date.today().isoformat()
    upcoming, past = [], []
    for a in appts:
        dt = _get(a, "StartDateTime", "startDateTime", "Date", "date")
        entry = {
            "appointment_id": _get(a, "AppointmentId", "appointmentId", "Id", "id"),
            "date": dt,
            "service": _get(a, "ServiceName", "serviceName", "Service", "service"),
            "staff": _get(a, "EmployeeName", "employeeName", "Employee", "employee"),
            "status": _get(a, "StatusDescription", "statusDescription", "Status", "status"),
            "duration_minutes": _get(a, "Duration", "duration", "DurationMinutes", "durationMinutes"),
        }
        if dt >= today:
            upcoming.append(entry)
        else:
            past.append(entry)
    return {"client_id": client_id, "upcoming_appointments": upcoming[:10], "recent_visits": past[-10:], "total_past_visits": len(past)}


@mcp.tool()
def check_availability(service_id: str, check_date: str = "", employee_id: str = "") -> dict:
    """Check available appointment times for a service on a given date (YYYY-MM-DD)."""
    d = check_date or date.today().isoformat()
    params = {"ServiceId": service_id, "Date": d}
    if employee_id:
        params["EmployeeId"] = employee_id
    data = meevo_get("/publicapi/v1/appointments/availabletimes", params)
    slots = data.get("AvailableTimes") or data.get("availableTimes") or data.get("Times") or data.get("times") or _items(data)
    return {"service_id": service_id, "date": d, "available_times": slots[:20], "total_slots": len(slots)}


@mcp.tool()
def book_appointment(client_id: str, service_id: str, start_datetime: str, employee_id: str = "", notes: str = "") -> dict:
    """Book a new appointment. start_datetime format: YYYY-MM-DDTHH:MM:SS."""
    service_entry = {"ServiceId": service_id, "StartDateTime": start_datetime}
    if employee_id:
        service_entry["EmployeeId"] = employee_id
    body = {"ClientId": client_id, "Services": [service_entry]}
    if notes:
        body["Notes"] = notes
    try:
        result = meevo_post("/publicapi/v1/appointments", body)
        appts = result.get("Appointments") or result.get("appointments") or [{}]
        appt_id = _get(result, "AppointmentId", "appointmentId", "Id", "id") or _get(appts[0], "AppointmentId", "appointmentId", "Id", "id")
        return {"success": True, "appointment_id": appt_id, "client_id": client_id, "service_id": service_id, "start_datetime": start_datetime, "raw": result}
    except requests.HTTPError as e:
        return {"success": False, "error": str(e), "response_body": e.response.text if e.response is not None else ""}


@mcp.tool()
def reschedule_appointment(appointment_id: str, new_start_datetime: str, employee_id: str = "") -> dict:
    """Reschedule an existing appointment. new_start_datetime format: YYYY-MM-DDTHH:MM:SS."""
    body = {"StartDateTime": new_start_datetime}
    if employee_id:
        body["EmployeeId"] = employee_id
    try:
        result = meevo_put(f"/publicapi/v1/appointments/{appointment_id}", body)
        return {"success": True, "appointment_id": appointment_id, "new_start_datetime": new_start_datetime, "raw": result}
    except requests.HTTPError as e:
        return {"success": False, "error": str(e), "response_body": e.response.text if e.response is not None else ""}


@mcp.tool()
def cancel_appointment(appointment_id: str, cancellation_reason: str = "") -> dict:
    """Cancel an existing appointment. Always confirm with the client first."""
    params = {}
    if cancellation_reason:
        params["CancellationReason"] = cancellation_reason
    try:
        result = meevo_delete(f"/publicapi/v1/appointments/{appointment_id}", params or None)
        return {"success": True, "appointment_id": appointment_id, "cancelled": True, "raw": result}
    except requests.HTTPError as e:
        return {"success": False, "error": str(e), "response_body": e.response.text if e.response is not None else ""}


@mcp.tool()
def list_services() -> dict:
    """List all services offered at the spa with IDs, durations, and prices."""
    try:
        all_services = []
        for page_num in range(1, 20):
            data = meevo_get("/publicapi/v1/services", {"pageNumber": page_num})
            batch = data.get("data") or data.get("Data") or _items(data)
            if not batch:
                break
            all_services.extend(batch)
            if len(batch) < 20:
                break
        result = []
        for s in all_services:
            result.append({
                "id": _str(s.get("id") or s.get("serviceId")),
                "name": _str(s.get("displayName") or s.get("serviceDisplayName") or s.get("name") or s.get("serviceName")),
                "category": _str(s.get("categoryName") or s.get("category") or s.get("categoryDisplayName")),
                "duration_minutes": _str(s.get("duration") or s.get("durationMinutes") or s.get("serviceDuration")),
                "price": _str(s.get("price") or s.get("retailPrice") or s.get("servicePrice")),
            })
        return {"services": result, "total": _str(len(result))}
    except requests.HTTPError as e:
        return {"error": str(e), "body": e.response.text[:500] if e.response else ""}


@mcp.tool()
def list_staff(page: int = 1) -> dict:
    """List all staff/employees at the spa with names and IDs."""
    data = meevo_get("/publicapi/v1/employees")
    staff = data.get("data") or data.get("Data") or _items(data)
    result = []
    for e in staff:
        cats = e.get("employeeCategories")
        if isinstance(cats, list) and cats and isinstance(cats[0], dict):
            title = _str(cats[0].get("employeeCategoryDisplayName"))
        else:
            title = ""
        result.append({
            "id": _str(e.get("id") or e.get("employeeId")),
            "name": (_str(e.get("firstName")) + " " + _str(e.get("lastName"))).strip(),
            "title": title,
            "is_active": "true",
        })
    return {"staff": result, "total": _str(len(staff))}


if __name__ == "__main__":
    mcp.settings.port = int(os.environ.get("PORT", 10000))
    mcp.run(transport="streamable-http")
