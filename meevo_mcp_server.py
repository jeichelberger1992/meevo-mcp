"""
Meevo MCP Server
================
Exposes Meevo API endpoints as MCP tools so Conduit's AI agent
can look up clients, appointments, and services in real-time,
and book, reschedule, or cancel appointments.

Version: v8 - fixed OB session auth (no Authorization header needed for OB session PATCH)
"""

import os
import re
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

# Derive the OB API base URL from BASE_URL (na2pub.meevo.com -> na2.meevo.com)
def _ob_base():
    host = BASE_URL.rstrip("/")
    host = re.sub(r'pub\.meevo\.com', '.meevo.com', host)
    host = re.sub(r'devpub\.meevodev\.com', '.meevodev.com', host)
    return host + "/onlinebooking/api/ob"

OB_BASE = _ob_base()

_token = None
_token_expiry = 0.0

_ob_token = None
_ob_token_expiry = 0.0


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


def get_ob_token():
    """Get a bearer token from the OB API session endpoint.
    The OB session endpoint is public — no Authorization header needed."""
    global _ob_token, _ob_token_expiry
    if _ob_token and time.time() < _ob_token_expiry:
        return _ob_token
    r = requests.patch(
        f"{OB_BASE}/session",
        json={"TenantId": int(TENANT_ID), "LocationId": int(LOCATION_ID)},
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        timeout=10,
    )
    r.raise_for_status()
    d = r.json()
    _ob_token = d.get("bearerToken") or d.get("BearerToken")
    _ob_token_expiry = time.time() + 1800
    return _ob_token


def _ob_headers():
    return {"Authorization": f"Bearer {get_ob_token()}", "Content-Type": "application/json", "Accept": "application/json"}


def meevo_get(path, params=None):
    base = {"tenantId": TENANT_ID, "locationId": LOCATION_ID}
    if params:
        base.update(params)
    r = requests.get(f"{BASE_URL}{path}", params=base, headers=_auth_headers(), timeout=15)
    r.raise_for_status()
    return r.json()


def _cap_params(extra=None):
    p = {"TenantId": int(TENANT_ID), "LocationId": int(LOCATION_ID)}
    if extra:
        p.update(extra)
    return p


def meevo_post(path, body):
    r = requests.post(f"{BASE_URL}{path}", params=_cap_params(), json=body, headers=_auth_headers(), timeout=15)
    r.raise_for_status()
    return r.json() if r.content else {"success": True}


def meevo_put(path, body, extra_params=None):
    r = requests.put(f"{BASE_URL}{path}", params=_cap_params(extra_params), json=body, headers=_auth_headers(), timeout=15)
    r.raise_for_status()
    return r.json() if r.content else {"success": True}


def meevo_delete(path, extra_params=None):
    r = requests.delete(f"{BASE_URL}{path}", params=_cap_params(extra_params), headers=_auth_headers(), timeout=15)
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
    return PlainTextResponse("OK v8")


@mcp.custom_route("/test_ob", methods=["GET"])
async def test_ob(request):
    from starlette.responses import JSONResponse
    try:
        r = requests.patch(
            f"{OB_BASE}/session",
            json={"TenantId": int(TENANT_ID), "LocationId": int(LOCATION_ID)},
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            timeout=10,
        )
        return JSONResponse({
            "ob_base": OB_BASE,
            "status": r.status_code,
            "body_snippet": r.text[:300],
            "request_headers": dict(r.request.headers),
        })
    except Exception as e:
        return JSONResponse({"ob_base": OB_BASE, "error": str(e)})


@mcp.tool()
def debug_ob_session() -> dict:
    """Debug the OB API session endpoint — tries multiple URL patterns and methods to find what works."""
    body = {"TenantId": int(TENANT_ID), "LocationId": int(LOCATION_ID)}
    tok = get_token()
    hdrs_bare = {"Content-Type": "application/json", "Accept": "application/json"}
    hdrs_auth = {**hdrs_bare, "Authorization": f"Bearer {tok}"}
    results = {"ob_base_derived": OB_BASE, "pub_base": BASE_URL, "body": body}

    candidates = [
        # (label, method, url)
        ("patch_na2_ob",    "PATCH", f"https://na2.meevo.com/onlinebooking/api/ob/session"),
        ("patch_na2pub_ob", "PATCH", f"https://na2pub.meevo.com/onlinebooking/api/ob/session"),
        ("post_na2_ob",     "POST",  f"https://na2.meevo.com/onlinebooking/api/ob/session"),
        ("patch_na2_v2",    "PATCH", f"https://na2.meevo.com/onlinebooking/api/v2/ob/session"),
        ("patch_na2_bare",  "PATCH", f"https://na2.meevo.com/ob/session"),
        ("patch_na2pub_v1", "PATCH", f"https://na2pub.meevo.com/api/ob/session"),
    ]
    for label, method, url in candidates:
        for auth_label, hdrs in [("no_auth", hdrs_bare), ("with_auth", hdrs_auth)]:
            key = f"{label}_{auth_label}"
            try:
                fn = requests.patch if method == "PATCH" else requests.post
                r = fn(url, json=body, headers=hdrs, timeout=8)
                results[key] = {"url": url, "status": r.status_code, "body": r.text[:300]}
                if r.status_code < 400:
                    results["SUCCESS"] = key
                    break
            except Exception as e:
                results[key] = {"url": url, "error": str(e)[:100]}
        if "SUCCESS" in results:
            break
    return results


@mcp.tool()
def debug_api(path: str) -> dict:
    """Call any Meevo API path (GET) and return the raw response."""
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
    """Search for Meevo clients by name, phone, or email. Fetches up to 100 pages and filters locally."""
    import re as _re
    clean_phone = _re.sub(r'\D', '', phone).lstrip('1')
    try:
        all_clients = []
        for page_num in range(1, 101):
            try:
                page_params = {"pageNumber": page_num}
                if last_name:
                    page_params["lastName"] = last_name
                data = meevo_get("/publicapi/v1/clients", page_params)
            except requests.HTTPError:
                if page_num > 1:
                    break
                raise
            batch = _items(data)
            if not batch:
                break
            all_clients.extend(batch)
            if len(batch) < 20:
                break
        matches = []
        for c in all_clients:
            c_last = _str(_get(c, "lastName", "LastName")).lower()
            c_first = _str(_get(c, "firstName", "FirstName")).lower()
            c_email = _str(_get(c, "emailAddress", "email", "Email", "EmailAddress")).lower()
            c_phones = c.get("phoneNumbers") or c.get("PhoneNumbers") or []
            c_phone_digits = [_re.sub(r'\D', '', _str(_get(p, "number", "Number", "phoneNumber", "PhoneNumber"))).lstrip('1') for p in c_phones]
            if last_name and last_name.lower() not in c_last:
                continue
            if first_name and first_name.lower() not in c_first:
                continue
            if email and email.lower() not in c_email:
                continue
            if clean_phone and not any(clean_phone in p or p in clean_phone for p in c_phone_digits if p):
                continue
            matches.append(c)
        if not matches:
            return {"found": False, "searched": len(all_clients), "message": f"No clients matching criteria in {len(all_clients)} records fetched."}
        results = []
        for c in matches[:5]:
            phones = c.get("phoneNumbers") or c.get("PhoneNumbers") or []
            results.append({
                "client_id": _get(c, "id", "clientId", "Id", "ClientId"),
                "name": f"{_get(c, 'firstName', 'FirstName')} {_get(c, 'lastName', 'LastName')}".strip(),
                "email": _get(c, "emailAddress", "email", "Email", "EmailAddress"),
                "phones": [_get(p, "number", "Number", "phoneNumber", "PhoneNumber") for p in phones],
            })
        return {"found": True, "clients": results, "total_matches": len(matches), "total_searched": len(all_clients)}
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
            "appointment_service_id": _get(a, "AppointmentServiceId", "appointmentServiceId"),
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
def check_availability(service_id: str, check_date: str = "", days_ahead: int = 7, employee_id: str = "") -> dict:
    """Check available appointment openings for a service using the Meevo Online Booking API.
    check_date is YYYY-MM-DD (defaults to today). Returns up to 20 openings per day."""
    start = check_date or date.today().isoformat()
    end = (date.fromisoformat(start) + timedelta(days=days_ahead)).isoformat()

    scan_svc = {
        "clientId": "00000000-0000-0000-0000-000000000000",
        "serviceId": service_id,
        "employeeId": employee_id if employee_id else None,
        "genderPreferenceEnum": 105,
        "clientFirstName": "Guest",
        "clientPhoneNumber": "0000000000",
        "clientCountryCode": "1",
        "isGuest": True,
        "customServiceStepTimings": None,
    }

    body = {
        "scanServices": [scan_svc],
        "payingClientId": None,
        "isRescan": False,
        "scanOrigin": 1,
        "maxOpeningsPerDay": 20,
        "appointmentBufferMinutes": 15,
        "maxStartTimeWait": 0,
        "maxWaitTimeBetweenServices": 0,
        "requireSameStartTime": True,
        "requireSameResource": False,
        "scanDateType": 2094,
        "scanTimeType": 2095,
        "startDate": f"{start}T00:00:00",
        "endDate": f"{end}T23:59:59",
        "isCouplesScan": False,
        "isRestrictedToBookableOnline": True,
    }

    try:
        r = requests.post(
            f"{OB_BASE}/scanforopenings",
            json=body,
            headers=_ob_headers(),
            timeout=20,
        )
        r.raise_for_status()
        groups = r.json()
        all_openings = []
        for group in (groups or []):
            for o in (group.get("serviceOpenings") or []):
                all_openings.append({
                    "date": (o.get("date") or "")[:10],
                    "start_time": (o.get("startTime") or "")[11:16],
                    "end_time": (o.get("endTime") or "")[11:16],
                    "employee_id": o.get("employeeId"),
                    "employee_name": o.get("employeeDisplayName") or o.get("employeeName") or "",
                    "service_name": o.get("serviceName"),
                    "price": o.get("serviceBasePrice"),
                })
        return {
            "service_id": service_id,
            "start": start,
            "end": end,
            "openings": all_openings[:50],
            "total": len(all_openings),
        }
    except requests.HTTPError as e:
        return {"error": str(e), "response_body": e.response.text[:500] if e.response is not None else ""}
    except Exception as e:
        return {"error": str(e), "service_id": service_id}


@mcp.tool()
def book_appointment(client_id: str, service_id: str, start_datetime: str, employee_id: str = "", notes: str = "") -> dict:
    """Book a new appointment. start_datetime format: YYYY-MM-DDTHH:MM:SS."""
    body = {
        "ClientId": client_id,
        "ServiceId": service_id,
        "StartDateTime": start_datetime,
    }
    if employee_id:
        body["EmployeeId"] = employee_id
    if notes:
        body["Notes"] = notes
    try:
        result = meevo_post("/publicapi/v1/book/service", body)
        appt_svc_id = _get(result, "AppointmentServiceId", "appointmentServiceId", "Id", "id")
        appt_id = _get(result, "AppointmentId", "appointmentId")
        return {"success": True, "appointment_service_id": appt_svc_id, "appointment_id": appt_id, "client_id": client_id, "service_id": service_id, "start_datetime": start_datetime, "raw": result}
    except requests.HTTPError as e:
        return {"success": False, "error": str(e), "response_body": e.response.text if e.response is not None else ""}


@mcp.tool()
def reschedule_appointment(appointment_service_id: str, new_start_datetime: str, employee_id: str = "") -> dict:
    """Reschedule an existing appointment service. new_start_datetime format: YYYY-MM-DDTHH:MM:SS."""
    try:
        svc = meevo_get(f"/publicapi/v1/book/service/{appointment_service_id}")
        row_version = _get(svc, "RowVersion", "rowVersion")
    except requests.HTTPError as e:
        return {"success": False, "error": f"Could not fetch appointment service: {e}", "response_body": e.response.text if e.response else ""}

    body = {"StartDateTime": new_start_datetime}
    if employee_id:
        body["EmployeeId"] = employee_id
    if row_version:
        body["RowVersion"] = row_version
    try:
        result = meevo_put(f"/publicapi/v1/book/service/{appointment_service_id}", body)
        return {"success": True, "appointment_service_id": appointment_service_id, "new_start_datetime": new_start_datetime, "raw": result}
    except requests.HTTPError as e:
        return {"success": False, "error": str(e), "response_body": e.response.text if e.response is not None else ""}


@mcp.tool()
def cancel_appointment(appointment_service_id: str, cancellation_reason_id: str = "") -> dict:
    """Cancel an existing appointment service. Always confirm with the client first."""
    try:
        svc = meevo_get(f"/publicapi/v1/book/service/{appointment_service_id}")
        row_version = _get(svc, "RowVersion", "rowVersion")
    except requests.HTTPError as e:
        return {"success": False, "error": f"Could not fetch appointment service: {e}", "response_body": e.response.text if e.response else ""}

    extra = {}
    if row_version:
        extra["RowVersion"] = row_version
    if cancellation_reason_id:
        extra["CancellationReasonId"] = cancellation_reason_id
    try:
        result = meevo_delete(f"/publicapi/v1/book/service/{appointment_service_id}", extra or None)
        return {"success": True, "appointment_service_id": appointment_service_id, "cancelled": True, "raw": result}
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
