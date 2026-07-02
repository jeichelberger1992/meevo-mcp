"""
Meevo MCP Server
================
Exposes Meevo API endpoints as MCP tools so Conduit's AI agent
can look up clients, appointments, and services in real-time,
and book, reschedule, or cancel appointments.

Version: v23 - SFTP-based appointment lookup via Meevo DDS feed
"""

import base64
import csv
import io
import os
import re
import tempfile
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

SFTP_HOST    = os.environ.get("MEEVO_SFTP_HOST",    "cdcsftp.meevo.com")
SFTP_USER    = os.environ.get("MEEVO_SFTP_USER",    "JacquelynsSpa")
SFTP_KEY_B64 = os.environ.get("MEEVO_SFTP_KEY_B64", "")  # base64-encoded RSA private key
SFTP_PATH    = os.environ.get("MEEVO_SFTP_PATH",    "/pmvo2-cdcsftp-storage01/MeevoTemp/SFTP/JacquelynsSpa")

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
    global _ob_token, _ob_token_expiry
    if _ob_token and time.time() < _ob_token_expiry:
        return _ob_token
    r = requests.patch(
        f"{OB_BASE}/session",
        json={"TenantId": int(TENANT_ID), "LocationId": int(LOCATION_ID)},
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": f"https://na2.meevo.com/CustomerPortal/onlinebooking/booking/services?tenantId={TENANT_ID}&locationId={LOCATION_ID}",
            "Origin": "https://na2.meevo.com",
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


def meevo_put(path, body, extra_params=None):
    r = requests.put(f"{BASE_URL}{path}", params=_cap_params(extra_params), json=body, headers=_auth_headers(), timeout=15)
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
    return PlainTextResponse("OK v24")


@mcp.custom_route("/test_ob", methods=["GET"])
async def test_ob(request):
    from starlette.responses import JSONResponse
    global _ob_token, _ob_token_expiry
    _ob_token = None
    _ob_token_expiry = 0.0
    try:
        tok = get_ob_token()
        return JSONResponse({"success": True, "ob_base": OB_BASE, "token_len": len(tok) if tok else 0})
    except Exception as e:
        return JSONResponse({"success": False, "ob_base": OB_BASE, "error": str(e)[:300]})


@mcp.tool()
def debug_ob_session() -> dict:
    """Debug the OB API session endpoint."""
    body = {"TenantId": int(TENANT_ID), "LocationId": int(LOCATION_ID)}
    tok = get_token()
    hdrs = {"Content-Type": "application/json", "Accept": "application/json", "Authorization": f"Bearer {tok}"}
    results = {"ob_base_derived": OB_BASE, "pub_base": BASE_URL}
    for label, method, url in [
        ("patch_na2_ob", "PATCH", "https://na2.meevo.com/onlinebooking/api/ob/session"),
        ("post_na2_ob",  "POST",  "https://na2.meevo.com/onlinebooking/api/ob/session"),
    ]:
        try:
            fn = requests.patch if method == "PATCH" else requests.post
            r = fn(url, json=body, headers=hdrs, timeout=8)
            results[label] = {"status": r.status_code, "body": r.text[:300]}
            if r.status_code < 400:
                results["SUCCESS"] = label
                break
        except Exception as e:
            results[label] = {"error": str(e)[:100]}
    return results


@mcp.tool()
def debug_api(path: str) -> dict:
    """Call any Meevo API path (GET) and return the raw response."""
    try:
        data = meevo_get(path)
        sample = data
        if isinstance(data, dict) and "data" in data and isinstance(data["data"], list) and data["data"]:
            sample = {"envelope_keys": list(data.keys()), "first_item": data["data"][0], "total": len(data["data"])}
        return {"path": path, "keys": list(data.keys()) if isinstance(data, dict) else None, "sample": str(sample)[:3000]}
    except requests.HTTPError as e:
        return {"error": str(e), "status": e.response.status_code if e.response else None, "body": e.response.text[:500] if e.response else ""}


@mcp.tool()
def search_clients(last_name: str = "", first_name: str = "", phone: str = "", email: str = "") -> dict:
    """Search for Meevo clients by name, phone, or email."""
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
            return {"found": False, "searched": len(all_clients), "message": f"No clients matching in {len(all_clients)} records."}
        out = []
        for c in matches[:5]:
            phones = c.get("phoneNumbers") or c.get("PhoneNumbers") or []
            out.append({
                "client_id": _get(c, "id", "clientId", "Id", "ClientId"),
                "name": f"{_get(c, 'firstName', 'FirstName')} {_get(c, 'lastName', 'LastName')}".strip(),
                "email": _get(c, "emailAddress", "email", "Email", "EmailAddress"),
                "phones": [_get(p, "number", "Number", "phoneNumber", "PhoneNumber") for p in phones],
            })
        return {"found": True, "clients": out, "total_matches": len(matches), "total_searched": len(all_clients)}
    except requests.HTTPError as e:
        return {"error": str(e), "body": e.response.text[:500] if e.response else ""}


@mcp.tool()
def lookup_client(phone: str = "", email: str = "") -> dict:
    """Look up a Meevo client by phone number or email address.

    IMPORTANT: Meevo's /clients endpoint does NOT reliably filter by PhoneNumber
    server-side — it ignores the param and returns the default first page. Blindly
    taking the first record is why unknown numbers used to resolve to the wrong
    client. So we page through clients and match locally on an EXACT phone (last 10
    digits) or exact email. Returns found=False when there is no real match so the
    agent creates a new client / asks for a name instead of guessing.
    """
    import re as _re
    if not phone and not email:
        return {"error": "Provide a phone number or email."}
    target_phone = _re.sub(r'\D', '', phone).lstrip('1')[-10:] if phone else ""
    target_email = email.strip().lower()

    all_clients = []
    try:
        page_num = 1
        page_size = None
        while page_num <= 500:
            try:
                data = meevo_get("/publicapi/v1/clients", {"pageNumber": page_num})
            except requests.HTTPError:
                if page_num > 1:
                    break
                raise
            batch = _items(data)
            if not batch:
                break
            all_clients.extend(batch)
            if page_size is None:
                page_size = len(batch)
            if len(batch) < (page_size or 1):
                break
            page_num += 1
    except requests.HTTPError as e:
        return {"error": str(e), "body": e.response.text[:500] if e.response else ""}

    def _phone_hit(c):
        if not target_phone:
            return False
        for p in (c.get("phoneNumbers") or c.get("PhoneNumbers") or []):
            digits = _re.sub(r'\D', '', _str(_get(p, "number", "Number", "phoneNumber", "PhoneNumber"))).lstrip('1')[-10:]
            if digits and digits == target_phone:
                return True
        return False

    def _email_hit(c):
        if not target_email:
            return False
        return _str(_get(c, "emailAddress", "email", "Email", "EmailAddress")).lower() == target_email

    matches = [c for c in all_clients if (_phone_hit(c) or _email_hit(c))]

    def _shape(c):
        phones = c.get("phoneNumbers") or c.get("PhoneNumbers") or []
        return {
            "client_id": _get(c, "ClientId", "clientId", "Id", "id"),
            "name": f"{_get(c, 'FirstName', 'firstName')} {_get(c, 'LastName', 'lastName')}".strip(),
            "email": _get(c, "Email", "email", "EmailAddress", "emailAddress"),
            "phones": [_get(p, "Number", "number", "PhoneNumber", "phoneNumber") for p in phones],
            "birth_date": _get(c, "BirthDate", "birthDate"),
            "notes": _get(c, "Notes", "notes"),
        }

    if not matches:
        return {"found": False, "searched": len(all_clients),
                "message": f"No client found for {phone or email}. Create a new client or ask the texter for their name — do NOT book under an existing client."}
    if len(matches) > 1:
        return {"found": True, "ambiguous": True, "match_count": len(matches),
                "clients": [_shape(c) for c in matches[:5]],
                "message": "Multiple clients share this contact info. Confirm the client's name before booking."}
    return {"found": True, **_shape(matches[0])}


def _sftp_connect():
    """Return an open paramiko SFTP client using the base64-encoded key from env."""
    import paramiko
    key_b64 = SFTP_KEY_B64
    if not key_b64:
        raise RuntimeError("MEEVO_SFTP_KEY_B64 env var not set")
    key_bytes = base64.b64decode(key_b64)
    key = paramiko.RSAKey.from_private_key(io.StringIO(key_bytes.decode()))
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(SFTP_HOST, username=SFTP_USER, pkey=key, timeout=20)
    return ssh, ssh.open_sftp()


@mcp.tool()
def get_client_appointments(client_id: str, start_date: str = "", end_date: str = "") -> dict:
    """Get upcoming appointments for a client via Meevo DDS SFTP feed.
    Returns appointment_service_id and concurrency_check_digits needed for cancel/reschedule.
    start_date/end_date: YYYY-MM-DD (defaults to today through next 90 days)."""
    sd = date.fromisoformat(start_date) if start_date else date.today()
    ed = date.fromisoformat(end_date) if end_date else (date.today() + timedelta(days=90))
    try:
        ssh, sftp = _sftp_connect()
    except Exception as e:
        return {"error": f"SFTP connect failed: {e}", "appointments": [], "count": 0}

    try:
        # List files in the SFTP directory
        files = sftp.listdir(SFTP_PATH)
        # Look for appointment-related files (csv/txt), prefer most recent
        appt_files = sorted(
            [f for f in files if "appoint" in f.lower() or "appt" in f.lower() or "booking" in f.lower()],
            reverse=True
        )
        if not appt_files:
            # Fall back: list all files and return them for discovery
            return {"error": "No appointment files found", "available_files": files[:30], "appointments": [], "count": 0}

        results = []
        tried_files = []
        for fname in appt_files[:3]:  # Try up to 3 most recent appointment files
            fpath = f"{SFTP_PATH}/{fname}"
            tried_files.append(fname)
            try:
                with sftp.open(fpath, "r") as f:
                    content = f.read().decode("utf-8", errors="replace")
                reader = csv.DictReader(io.StringIO(content))
                for row in reader:
                    # Match by client_id (try multiple column names)
                    row_client = (row.get("ClientId") or row.get("clientId") or
                                  row.get("ClientGuid") or row.get("clientGuid") or "")
                    if row_client.lower() != client_id.lower():
                        continue
                    # Parse date
                    appt_dt_str = (row.get("StartTime") or row.get("startTime") or
                                   row.get("StartDate") or row.get("startDate") or "")
                    try:
                        appt_dt = date.fromisoformat(appt_dt_str[:10])
                        if not (sd <= appt_dt <= ed):
                            continue
                    except Exception:
                        pass  # Include if we can't parse the date
                    results.append({
                        "appointment_id": row.get("AppointmentId") or row.get("appointmentId") or "",
                        "appointment_service_id": (row.get("AppointmentServiceId") or
                                                   row.get("appointmentServiceId") or ""),
                        "service_name": row.get("ServiceName") or row.get("serviceName") or "",
                        "employee_name": (row.get("EmployeeName") or row.get("employeeName") or
                                          row.get("EmployeeFirstName") or ""),
                        "start_time": appt_dt_str,
                        "status": row.get("Status") or row.get("status") or "",
                        "concurrency_check_digits": (row.get("ConcurrencyCheckDigits") or
                                                     row.get("concurrencyCheckDigits") or
                                                     row.get("RowVersion") or ""),
                    })
                if results:
                    break
            except Exception as e:
                tried_files[-1] += f" (err: {e})"

        # If no results yet, return sample of first file for discovery
        sample = {}
        if not results and appt_files:
            try:
                with sftp.open(f"{SFTP_PATH}/{appt_files[0]}", "r") as f:
                    sample_lines = f.read(2000).decode("utf-8", errors="replace")
                sample = {"first_file_sample": sample_lines}
            except Exception:
                pass

        return {
            "appointments": results,
            "count": len(results),
            "date_range": f"{sd} to {ed}",
            "files_tried": tried_files,
            **sample,
        }
    finally:
        sftp.close()
        ssh.close()


def _get_client_appointments_api(client_id: str, start_date: str = "", end_date: str = "") -> dict:
    """Get upcoming/recent appointments for a client. Returns appointment_service_id and concurrency_check_digits
    needed for cancel or reschedule. start_date/end_date: YYYY-MM-DD (defaults to next 90 days)."""
    sd = start_date or date.today().isoformat()
    ed = end_date or (date.today() + timedelta(days=90)).isoformat()
    results = []
    tried = {}

    def _parse_appts(data):
        out = []
        items = data.get("data") or data.get("Data") or _items(data)
        if not items:
            return out
        for appt in items:
            svc_list = appt.get("appointmentServices") or appt.get("AppointmentServices") or [appt]
            for svc in svc_list:
                out.append({
                    "appointment_id": _str(_get(appt, "appointmentId", "AppointmentId", "id", "Id")),
                    "appointment_service_id": _str(_get(svc, "appointmentServiceId", "AppointmentServiceId", "id", "Id")),
                    "service_name": _str(_get(svc, "serviceName", "ServiceName", "serviceDisplayName")),
                    "employee_name": _str(_get(svc, "employeeName", "EmployeeName", "employeeDisplayName")),
                    "start_time": _str(_get(svc, "startTime", "StartTime", "startDateTime", "StartDateTime")),
                    "status": _str(_get(svc, "status", "Status", "appointmentStatus")),
                    "concurrency_check_digits": _str(_get(svc, "concurrencyCheckDigits", "ConcurrencyCheckDigits", "rowVersion", "RowVersion")),
                })
        return out

    # Try publicapi paths
    for path, params in [
        ("/publicapi/v1/appointments", {"clientId": client_id, "startDate": sd, "endDate": ed}),
        ("/publicapi/v1/appointments", {"ClientId": client_id, "StartDate": sd, "EndDate": ed}),
        (f"/publicapi/v1/clients/{client_id}/appointments", {"startDate": sd, "endDate": ed}),
        ("/publicapi/v1/book/service", {"clientId": client_id, "startDate": sd, "endDate": ed, "TenantId": TENANT_ID, "LocationId": LOCATION_ID}),
        ("/publicapi/v1/bookings", {"clientId": client_id, "startDate": sd, "endDate": ed}),
        (f"/publicapi/v1/clients/{client_id}/bookings", {"startDate": sd, "endDate": ed}),
    ]:
        try:
            data = meevo_get(path, params)
            parsed = _parse_appts(data)
            if parsed:
                tried[path] = f"ok ({len(parsed)} appts)"
                results = parsed
                break
            else:
                tried[path] = f"empty: {str(data)[:100]}"
        except requests.HTTPError as e:
            tried[path] = f"{e.response.status_code}: {e.response.text[:100]}" if e.response else str(e)[:80]
        except Exception as e:
            tried[path] = str(e)[:80]

    # If still nothing, try OB API
    if not results:
        try:
            r = requests.get(
                f"{OB_BASE}/appointments",
                params={"ClientId": client_id, "TenantId": TENANT_ID, "LocationId": LOCATION_ID,
                        "StartDate": sd, "EndDate": ed},
                headers=_ob_headers(), timeout=15
            )
            tried["ob_appointments"] = f"{r.status_code}: {r.text[:100]}"
            if r.ok:
                parsed = _parse_appts(r.json() if r.content else {})
                if parsed:
                    results = parsed
        except Exception as e:
            tried["ob_appointments"] = str(e)[:80]

    return {"appointments": results, "count": len(results), "date_range": f"{sd} to {ed}", "tried": tried}


@mcp.tool()
def check_availability(service_id: str, check_date: str = "", days_ahead: int = 7, employee_id: str = "") -> dict:
    """Check available appointment slots. check_date is YYYY-MM-DD (defaults today). Returns up to 200 openings."""
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
        "maxOpeningsPerDay": 100,
        "appointmentBufferMinutes": 0,
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
        r = requests.post(f"{OB_BASE}/scanforopenings", json=body, headers=_ob_headers(), timeout=20)
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
                    "resource_id": o.get("resourceId") or o.get("ResourceId") or "",
                    "resource_name": o.get("resourceName") or o.get("ResourceName") or "",
                    "concurrency_check_digits": o.get("concurrencyCheckDigits") or o.get("ConcurrencyCheckDigits") or "",
                    "service_name": o.get("serviceName"),
                    "price": o.get("serviceBasePrice"),
                })
        return {"service_id": service_id, "start": start, "end": end, "openings": all_openings[:200], "total": len(all_openings)}
    except requests.HTTPError as e:
        return {"error": str(e), "response_body": e.response.text[:500] if e.response is not None else ""}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def list_resources() -> dict:
    """List bookable resources (rooms, booths) for services like spray tans."""
    results = {}
    for path in ["/publicapi/v1/resources", "/publicapi/v1/resource", "/publicapi/v1/resourcetypes"]:
        try:
            data = meevo_get(path)
            items = data.get("data") or data.get("Data") or _items(data)
            out = [{"id": _str(r.get("id") or r.get("resourceId")), "name": _str(r.get("name") or r.get("displayName"))} for r in (items if isinstance(items, list) else [])]
            results[path] = {"status": "ok", "resources": out}
        except requests.HTTPError as e:
            results[path] = {"status": e.response.status_code if e.response else "error"}
    try:
        r = requests.get(f"{OB_BASE}/resources", params={"TenantId": TENANT_ID, "LocationId": LOCATION_ID}, headers=_ob_headers(), timeout=10)
        results["ob_resources"] = {"status": r.status_code, "body": r.text[:500]}
    except Exception as e:
        results["ob_resources"] = {"error": str(e)[:200]}
    return results


@mcp.tool()
def book_appointment(client_id: str, service_id: str, start_datetime: str, employee_id: str = "", resource_id: str = "", concurrency_check_digits: str = "", notes: str = "") -> dict:
    """Book a new appointment. start_datetime: YYYY-MM-DDTHH:MM:SS.
    Pass resource_id and concurrency_check_digits from check_availability."""
    body = {
        "ClientId": client_id,
        "ServiceId": service_id,
        "StartTime": start_datetime,
        "SendConfirmation": True,
        "SendClientNotification": True,
        "NotifyClient": True,
        "BookingSource": 2,
    }
    if employee_id:
        body["EmployeeId"] = employee_id
    if resource_id:
        body["ResourceId"] = resource_id
    if concurrency_check_digits:
        body["ConcurrencyCheckDigits"] = concurrency_check_digits
    if notes:
        body["Notes"] = notes
    try:
        r = requests.post(f"{BASE_URL}/publicapi/v1/book/service", params=_cap_params(), json=body, headers=_auth_headers(), timeout=15)
        r.raise_for_status()
        result = r.json() if r.content else {"success": True}
        return {"success": True,
                "appointment_service_id": _get(result, "AppointmentServiceId", "appointmentServiceId", "Id", "id"),
                "appointment_id": _get(result, "AppointmentId", "appointmentId"),
                "raw": result}
    except requests.HTTPError as e:
        return {"success": False, "error": str(e), "response_body": e.response.text if e.response is not None else ""}


@mcp.tool()
def reschedule_appointment(appointment_service_id: str, new_start_datetime: str, employee_id: str = "", concurrency_check_digits: str = "") -> dict:
    """Reschedule an existing appointment. new_start_datetime: YYYY-MM-DDTHH:MM:SS.
    Get concurrency_check_digits from get_client_appointments first."""
    concurrency = concurrency_check_digits
    if not concurrency:
        try:
            svc = meevo_get(f"/publicapi/v1/book/service/{appointment_service_id}")
            concurrency = _get(svc, "ConcurrencyCheckDigits", "concurrencyCheckDigits", "RowVersion", "rowVersion")
        except requests.HTTPError as e:
            return {"success": False, "error": f"Could not fetch service: {e}", "response_body": e.response.text if e.response else ""}
    body = {"StartTime": new_start_datetime}
    if employee_id:
        body["EmployeeId"] = employee_id
    if concurrency:
        body["ConcurrencyCheckDigits"] = concurrency
    try:
        result = meevo_put(f"/publicapi/v1/book/service/{appointment_service_id}", body)
        return {"success": True, "appointment_service_id": appointment_service_id, "new_start_datetime": new_start_datetime, "raw": result}
    except requests.HTTPError as e:
        return {"success": False, "error": str(e), "response_body": e.response.text if e.response is not None else ""}


@mcp.tool()
def cancel_appointment(appointment_service_id: str, cancellation_reason_id: str = "", concurrency_check_digits: str = "") -> dict:
    """Cancel an appointment. Always confirm with client first.
    Get concurrency_check_digits from get_client_appointments first."""
    concurrency = concurrency_check_digits
    if not concurrency:
        try:
            svc = meevo_get(f"/publicapi/v1/book/service/{appointment_service_id}")
            concurrency = _get(svc, "ConcurrencyCheckDigits", "concurrencyCheckDigits", "RowVersion", "rowVersion")
        except requests.HTTPError as e:
            return {"success": False, "error": f"Could not fetch service: {e}", "response_body": e.response.text if e.response else ""}
    params = {"TenantId": int(TENANT_ID), "LocationId": int(LOCATION_ID)}
    if concurrency:
        params["ConcurrencyCheckDigits"] = concurrency
    if cancellation_reason_id:
        params["CancellationReasonId"] = cancellation_reason_id
    try:
        r = requests.delete(f"{BASE_URL}/publicapi/v1/book/service/{appointment_service_id}",
                            params=params, headers=_auth_headers(), timeout=15)
        r.raise_for_status()
        return {"success": True, "appointment_service_id": appointment_service_id, "cancelled": True}
    except requests.HTTPError as e:
        return {"success": False, "error": str(e), "response_body": e.response.text if e.response is not None else ""}


@mcp.tool()
def list_services() -> dict:
    """List all services at the spa with IDs, durations, and prices."""
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
        result = [{"id": _str(s.get("id") or s.get("serviceId")),
                   "name": _str(s.get("displayName") or s.get("serviceDisplayName") or s.get("name")),
                   "category": _str(s.get("categoryName") or s.get("category")),
                   "duration_minutes": _str(s.get("duration") or s.get("durationMinutes")),
                   "price": _str(s.get("price") or s.get("retailPrice"))} for s in all_services]
        return {"services": result, "total": str(len(result))}
    except requests.HTTPError as e:
        return {"error": str(e), "body": e.response.text[:500] if e.response else ""}


@mcp.tool()
def list_staff(page: int = 1) -> dict:
    """List all staff/employees at the spa."""
    data = meevo_get("/publicapi/v1/employees")
    staff = data.get("data") or data.get("Data") or _items(data)
    result = []
    for e in staff:
        cats = e.get("employeeCategories")
        title = _str(cats[0].get("employeeCategoryDisplayName")) if (isinstance(cats, list) and cats and isinstance(cats[0], dict)) else ""
        result.append({"id": _str(e.get("id") or e.get("employeeId")),
                       "name": (_str(e.get("firstName")) + " " + _str(e.get("lastName"))).strip(),
                       "title": title})
    return {"staff": result, "total": str(len(staff))}


@mcp.tool()
def debug_scan_raw(service_id: str) -> dict:
    """Return raw scan opening fields for a service to inspect resource IDs and structure."""
    start = date.today().isoformat()
    end = (date.today() + timedelta(days=3)).isoformat()
    scan_svc = {"clientId": "00000000-0000-0000-0000-000000000000", "serviceId": service_id,
                "employeeId": None, "genderPreferenceEnum": 105, "clientFirstName": "Guest",
                "clientPhoneNumber": "0000000000", "clientCountryCode": "1", "isGuest": True, "customServiceStepTimings": None}
    body = {"scanServices": [scan_svc], "payingClientId": None, "isRescan": False, "scanOrigin": 1,
            "maxOpeningsPerDay": 5, "appointmentBufferMinutes": 0, "maxStartTimeWait": 0,
            "maxWaitTimeBetweenServices": 0, "requireSameStartTime": True, "requireSameResource": False,
            "scanDateType": 2094, "scanTimeType": 2095, "startDate": f"{start}T00:00:00",
            "endDate": f"{end}T23:59:59", "isCouplesScan": False, "isRestrictedToBookableOnline": True}
    try:
        r = requests.post(f"{OB_BASE}/scanforopenings", json=body, headers=_ob_headers(), timeout=20)
        r.raise_for_status()
        groups = r.json()
        if not groups:
            return {"groups": 0}
        g0 = groups[0]
        openings = g0.get("serviceOpenings") or []
        o0 = openings[0] if openings else {}
        return {"opening_keys": list(o0.keys()), "opening_sample": o0, "total_groups": len(groups), "total_openings": len(openings)}
    except requests.HTTPError as e:
        return {"error": str(e), "body": e.response.text[:500] if e.response else ""}


@mcp.tool()
def get_ob_resources(service_id: str = "") -> dict:
    """Try to fetch resource list from OB API endpoints."""
    results = {}
    for path in ["/resources", f"/services/{service_id}/resources" if service_id else None]:
        if not path:
            continue
        try:
            r = requests.get(f"{OB_BASE}{path}", params={"TenantId": TENANT_ID, "LocationId": LOCATION_ID}, headers=_ob_headers(), timeout=10)
            results[path] = {"status": r.status_code, "body": r.text[:800]}
        except Exception as e:
            results[path] = {"error": str(e)[:100]}
    return results


@mcp.tool()
def send_appointment_notification(appointment_id: str = "", appointment_service_id: str = "", client_id: str = "") -> dict:
    """Re-trigger Meevo native SMS/email notification for an appointment."""
    results = {}
    body = {"AppointmentId": appointment_id, "ClientId": client_id, "SendSms": True, "SendEmail": True}
    for path in [f"/publicapi/v1/appointments/{appointment_id}/notify",
                 f"/publicapi/v1/appointments/{appointment_id}/sendconfirmation",
                 f"/publicapi/v1/book/service/{appointment_service_id}/notify"]:
        try:
            r = requests.post(f"{BASE_URL}{path}", params=_cap_params(), json=body, headers=_auth_headers(), timeout=10)
            results[path] = {"status": r.status_code, "body": r.text[:300]}
        except Exception as e:
            results[path] = {"error": str(e)[:100]}
    return results


if __name__ == "__main__":
    mcp.settings.port = int(os.environ.get("PORT", 10000))
    mcp.run(transport="streamable-http")
