"""Vehicle rental fleet API.

A small web service that lets a front end (a website or app) browse a rental
fleet, rent and return vehicles, manage the fleet, and log customers in.

Data is held in two spreadsheet-style (CSV) files that must sit next to this
script:
    vehicle.csv   - the fleet, one row per vehicle
    customer.csv  - customer details, including salted password hashes. New
                    customers created via the signup request are added here.

This file is a Flask "blueprint": a bundle of web addresses that is plugged into
the main application with   app.register_blueprint(api_bp)
For quick standalone testing it can also be started directly with:
    python vehicle_api.py
"""
import csv          # reading and writing the spreadsheet-style data files
import os           # file and folder handling
import secrets      # secure random values and safe comparisons (used for logins)
import tempfile     # temporary files, used to save data safely
import threading    # used to stop two requests changing the data at the same moment

from flask import Blueprint, request
from flask_restful import Api, Resource, abort

# Password hashing function, supplied by the login logic module. It takes a
# password and a "salt" (a random string unique to each customer) and returns
# the scrambled ("hashed") version of the password. Passwords are never stored
# or compared in their original form.
from loginLogic import hashPassword, generateSalt

# --------------------------------------------------------------------------
# Configuration
# Settings that are likely to change live here, in one place.
# --------------------------------------------------------------------------

# Every web address in this service starts with this prefix. The "v1" allows a
# future version 2 to be introduced without breaking existing front ends.
API_PREFIX = "/api/v1"

# The data files (looked for in the folder the service is started from).
VEHICLE_CSV = "vehicle.csv"
CUSTOMER_CSV = "customer.csv"

# Prototype rule: a customer is treated as an admin if their email address
# ends with this text. A more robust rule can replace this later.
ADMIN_EMAIL_DOMAIN = "@car-go.com"

# The states a vehicle can be in. Defined once so the spelling is consistent
# everywhere it is used.
#   AVAILABLE  - in the fleet and ready to rent
#   RENTED     - currently out with a customer
#   SERVICEREQ - needs servicing; cannot be rented until a worker sets it
#                back to AVAILABLE (using the returns request)
STATUS_AVAILABLE = "AVAILABLE"
STATUS_RENTED = "RENTED"
STATUS_SERVICE_REQUIRED = "SERVICEREQ"
VALID_STATUSES = {STATUS_AVAILABLE, STATUS_RENTED, STATUS_SERVICE_REQUIRED}

# A worker inspects the vehicle and decides which of these two states it goes
# into. (RENTED is not a possible outcome of a return.)
RETURN_OUTCOMES = {STATUS_AVAILABLE, STATUS_SERVICE_REQUIRED}

# The columns of vehicle.csv, in the order they are stored in the file.
FIELDS = [
    "id", "make", "model", "colour", "vin", "year", "vrm",
    "category", "numberSeats", "dayRate", "status", "fuelEconomy", "branch",
]

# Fields only admins may see. Everyone else gets vehicle data without these.
ADMIN_ONLY_FIELDS = {"vin", "vrm"}

# Values in a CSV file are all plain text. This lists the columns that must be
# converted into numbers (whole numbers "int", or decimals "float") on loading.
TYPE_CASTS = {
    "id": int, "year": int, "numberSeats": int,
    "dayRate": float, "fuelEconomy": float,
}

# Search options for the vehicle list. These are added to the end of the web
# address, e.g. /vehicles?branch=London&min_year=2020
#
# Text filters: must match exactly (capital letters are ignored).
TEXT_FILTERS = ["make", "model", "colour", "category", "branch", "status"]
# Range filters: each one supports a min_ and a max_ version, e.g. min_seats
# and max_seats. Each entry is: search name -> (column in vehicle.csv, number type).
RANGE_FILTERS = {
    "year": ("year", int),
    "seats": ("numberSeats", int),
    "day_rate": ("dayRate", float),
    "fuel_economy": ("fuelEconomy", float),
}

# A "blueprint" is a self-contained bundle of web addresses that gets plugged
# into the main application, rather than being an application itself. Every
# address in this bundle automatically starts with API_PREFIX. "api" is the
# part that connects each address to the code that handles it (registered at
# the bottom of the file).
api_bp = Blueprint("api", __name__, url_prefix=API_PREFIX)
api = Api(api_bp)

# --------------------------------------------------------------------------
# Data access
# The CSV files are read once when the service starts and kept in memory, which
# keeps requests fast. Any change to the fleet is written back to the file
# straight away, so nothing is lost if the service stops.
# --------------------------------------------------------------------------

# A "lock" works like a single-occupancy door. Code that changes the fleet
# must hold the lock while it works, so two requests arriving at the same time
# (for example two people renting the same car) are handled one after the
# other rather than tripping over each other.
_lock = threading.Lock()


def _parse_row(row):
    """Turn one raw row from vehicle.csv into a clean vehicle record."""
    vehicle = {}
    for field in FIELDS:
        # Missing cells are treated as empty text, and stray spaces are removed.
        raw = (row.get(field) or "").strip()
        cast = TYPE_CASTS.get(field)
        if cast is None:
            # Text column (e.g. make, model): keep as it is.
            vehicle[field] = raw
        else:
            # Number column: convert the text to a number. A blank cell (e.g.
            # no fuel economy for an electric car) becomes None, meaning "no value".
            vehicle[field] = cast(raw) if raw != "" else None
    # Tolerate 'Available' or 'available' in the file by standardising to capitals.
    vehicle["status"] = vehicle["status"].upper()
    return vehicle


def _detect_delimiter(path):
    """Work out whether a data file separates its columns with commas or tabs.

    Spreadsheet programs can save either way, so the header (first) line is
    checked and whichever separator appears more often is used.
    """
    # "utf-8-sig" ignores a hidden marker that Excel sometimes adds to the very
    # start of a file. Left in, that marker would corrupt the first column name.
    with open(path, newline="", encoding="utf-8-sig") as f:
        header = f.readline()
    return "\t" if header.count("\t") > header.count(",") else ","


# Detect the separator once, and reuse it when saving so the file keeps its format.
CSV_DELIMITER = _detect_delimiter(VEHICLE_CSV)


def load_vehicles():
    """Read every vehicle from vehicle.csv into a list held in memory."""
    with open(VEHICLE_CSV, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f, delimiter=CSV_DELIMITER)
        # Tidy the column names (remove stray spaces).
        reader.fieldnames = [name.strip() for name in reader.fieldnames or []]
        # Stop straight away, with a clear message, if an expected column is
        # missing. Otherwise the service would run but quietly return empty data.
        missing = [field for field in FIELDS if field not in reader.fieldnames]
        if missing:
            raise RuntimeError(f"{VEHICLE_CSV} is missing column(s): {', '.join(missing)}")
        return [_parse_row(row) for row in reader]


def save_vehicles():
    """Write the in-memory vehicle list back to vehicle.csv.

    Must only be called while holding the lock (see above).
    """
    # The new data is written to a temporary file first and then swapped in
    # for the real file in one step. This way, if something goes wrong part
    # way through saving, the original file is never left half-written.
    directory = os.path.dirname(os.path.abspath(VEHICLE_CSV))
    fd, tmp_path = tempfile.mkstemp(dir=directory, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDS, delimiter=CSV_DELIMITER)
            writer.writeheader()
            for vehicle in VEHICLES:
                # "No value" is written back as a blank cell.
                writer.writerow({k: ("" if v is None else v) for k, v in vehicle.items()})
        os.replace(tmp_path, VEHICLE_CSV)
    except Exception:
        # Clean up the temporary file, then pass the error on.
        os.unlink(tmp_path)
        raise


# The fleet, loaded when the service starts. All vehicle requests use this list.
VEHICLES = load_vehicles()


# The columns of customer.csv, in the order they are stored in the file. Used
# when a new row is written by the signup request.
CUSTOMER_FIELDS = [
    "customerId", "first_name", "last_name", "dob", "gender", "email",
    "address", "city", "country", "drivingLicenseNumber", "passportNumber",
    "LicenseRetrictions", "ip_address_v4", "ip_address_v6",
    "password", "hashedPassword", "salt",
]

# The customer columns this service needs to already be there. customer.csv
# may contain others; the ones above are expected once signup can write rows.
CUSTOMER_REQUIRED_COLUMNS = ["customerId", "email", "hashedPassword", "salt"]

# Detect the separator once, and reuse it when saving so the file keeps its format.
CUSTOMER_CSV_DELIMITER = _detect_delimiter(CUSTOMER_CSV)


def load_customers():
    """Read every customer from customer.csv into a list held in memory."""
    with open(CUSTOMER_CSV, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f, delimiter=CUSTOMER_CSV_DELIMITER)
        reader.fieldnames = [name.strip() for name in reader.fieldnames or []]
        # As with vehicles: fail loudly at startup if a needed column is missing.
        missing = [c for c in CUSTOMER_REQUIRED_COLUMNS if c not in reader.fieldnames]
        if missing:
            raise RuntimeError(f"{CUSTOMER_CSV} is missing column(s): {', '.join(missing)}")
        # Rows with no email address are skipped, since they cannot be logged
        # in with or looked up by anyone.
        return [row for row in reader if (row.get("email") or "").strip()]


def save_customers():
    """Write the in-memory customer list back to customer.csv.

    Must only be called while holding the lock (see above). Follows the same
    safe "write to a temporary file, then swap it in" approach as save_vehicles.
    """
    directory = os.path.dirname(os.path.abspath(CUSTOMER_CSV))
    fd, tmp_path = tempfile.mkstemp(dir=directory, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=CUSTOMER_FIELDS, delimiter=CUSTOMER_CSV_DELIMITER)
            writer.writeheader()
            for customer in CUSTOMERS:
                writer.writerow({field: customer.get(field, "") for field in CUSTOMER_FIELDS})
        os.replace(tmp_path, CUSTOMER_CSV)
    except Exception:
        os.unlink(tmp_path)
        raise


def find_customer_by_email(email):
    """Return the customer with this email (case-insensitive), or None."""
    email = email.strip().lower()
    for customer in CUSTOMERS:
        if customer["email"].strip().lower() == email:
            return customer
    return None


# The customers, loaded when the service starts. Signing up a new customer
# adds to this list and writes it back to customer.csv (see SignupCollection).
CUSTOMERS = load_customers()

# --------------------------------------------------------------------------
# Authentication and authorisation
#
# Two separate questions are answered here:
#   Authentication - "who are you?"       (logging in with email and password)
#   Authorisation  - "what may you do?"   (admins can do more than other users)
#
# How it works: after a successful login the caller is given a "token", a long
# random string that acts like a temporary ID badge. The caller shows it on
# every later request in a header:   Authorization: Bearer <token>
# The service looks the token up to find out who is calling and whether they
# are an admin. The admin status is stored on the server, never in the token,
# so a caller cannot make themselves an admin by editing their token.
# --------------------------------------------------------------------------

# The list of currently logged-in users: token -> customer id and admin status.
# It lives in memory only, so everyone has to log in again whenever the
# service restarts. Tokens never expire. That is acceptable for a prototype.
SESSIONS = {}


def get_role(lenient=False):
    """Work out who is calling. Returns 'admin', 'user', or None (not logged in).

    Normally, a token that is present but not recognised (wrong, or cleared by
    a restart) is rejected with a 401 "unauthorised" error. With lenient=True
    such a token is treated as if none was sent. That mode is used by the
    public vehicle pages, which should still work for anyone.
    """
    header = request.headers.get("Authorization")
    if not header:
        # No token sent: an anonymous visitor.
        return None
    # The header looks like "Bearer abc123": split it into the word "Bearer"
    # and the token itself, then look the token up among the logged-in users.
    scheme, _, token = header.partition(" ")
    session = SESSIONS.get(token.strip()) if scheme.lower() == "bearer" else None
    if session is None:
        if lenient:
            return None
        abort(401, message="Invalid or expired token. Log in again via POST /authentications.")
    return "admin" if session["admin"] else "user"


def require_admin():
    """Stop the request unless the caller is a logged-in admin.

    Two different errors are used on purpose:
      401 - not logged in (the caller should log in and try again)
      403 - logged in, but not allowed (logging in again would not help)
    """
    role = get_role()
    if role is None:
        abort(401, message="Authentication required: send 'Authorization: Bearer <token>'.")
    if role != "admin":
        abort(403, message="Admin access required.")


def is_admin(customer):
    """Decide whether a customer is an admin (prototype rule: email domain)."""
    return customer["email"].strip().lower().endswith(ADMIN_EMAIL_DOMAIN)


# --------------------------------------------------------------------------
# Helpers
# Small shared functions used by the request handlers further down.
# --------------------------------------------------------------------------


def present(vehicle, role):
    """Prepare a vehicle for sending back to the caller.

    Admins receive every field. Everyone else receives the vehicle with the
    admin-only fields (VIN and registration) removed.
    """
    if role == "admin":
        # A copy is returned so the master record cannot be changed by accident.
        return dict(vehicle)
    return {k: v for k, v in vehicle.items() if k not in ADMIN_ONLY_FIELDS}


def find_vehicle(vehicle_id):
    """Return the vehicle with the given id, or send a 404 "not found" error."""
    for vehicle in VEHICLES:
        if vehicle["id"] == vehicle_id:
            return vehicle
    abort(404, message=f"Vehicle {vehicle_id} not found.")


def parse_number_arg(name, cast):
    """Read a numeric search option from the web address (e.g. min_year=2020).

    Returns None if the option was not supplied, or a 400 "bad request" error
    if it was supplied but is not a valid number.
    """
    raw = request.args.get(name)
    if raw is None or raw == "":
        return None
    try:
        return cast(raw)
    except ValueError:
        abort(400, message=f"Query parameter '{name}' must be a {cast.__name__}.")


def parse_vehicle_id(data):
    """Read and check the vehicleId sent in the body of a rent or return request."""
    if not isinstance(data, dict) or "vehicleId" not in data:
        abort(400, message="Request body must be JSON containing 'vehicleId'.")
    vehicle_id = data["vehicleId"]
    # In Python, true/false also count as numbers (true == 1), so they are
    # excluded explicitly to avoid a request like {"vehicleId": true} slipping through.
    if isinstance(vehicle_id, bool) or not isinstance(vehicle_id, int):
        abort(400, message="'vehicleId' must be an integer.")
    return vehicle_id


def parse_return_status(data):
    """Read the status chosen by the worker who inspected the vehicle.

    This is required (there is deliberately no default, so a vehicle can never
    go back into the fleet by accident) and must be AVAILABLE or SERVICEREQ.
    Capital letters are ignored.
    """
    status = data.get("status")
    if not isinstance(status, str) or status.strip().upper() not in RETURN_OUTCOMES:
        abort(400, message=f"'status' is required and must be one of: {', '.join(sorted(RETURN_OUTCOMES))}.")
    return status.strip().upper()


# The details a new customer must supply to sign up. IP addresses are left
# blank, since they are not something a customer types in themselves.
SIGNUP_REQUIRED_FIELDS = [
    "first_name", "last_name", "dob", "gender", "email", "address", "city",
    "country", "drivingLicenseNumber", "passportNumber", "password",
]


def parse_signup_data(data):
    """Check the JSON body for a signup request and return the clean fields.

    licenseRestrictions is optional (not every driver has one); everything
    else in SIGNUP_REQUIRED_FIELDS must be supplied as non-empty text.
    """
    if not isinstance(data, dict):
        abort(400, message="Request body must be a JSON object.")

    errors, customer = {}, {}
    for field in SIGNUP_REQUIRED_FIELDS:
        value = data.get(field)
        if not isinstance(value, str) or not value.strip():
            errors[field] = f"'{field}' is required"
        else:
            customer[field] = value.strip()

    # This field keeps its CSV column's unusual spelling ("Restrictions" is
    # misspelt in the file), but is accepted from the request either way.
    restrictions = data.get("licenseRestrictions", data.get("LicenseRetrictions", ""))
    customer["LicenseRetrictions"] = restrictions.strip() if isinstance(restrictions, str) else ""

    if errors:
        abort(400, message="; ".join(errors.values()))
    return customer


def parse_new_vehicle(data):
    """Check the details sent to add a vehicle, and return a clean vehicle record.

    Every problem found is collected and reported together, so the caller can
    fix everything in one go rather than one error at a time.
    """
    if not isinstance(data, dict):
        abort(400, message="Request body must be a JSON object.")

    errors, vehicle = [], {}
    for field in FIELDS:
        # The id is generated by the service, and the status is handled below.
        if field in ("id", "status"):
            continue
        value = data.get(field)
        missing = value is None or (isinstance(value, str) and not value.strip())
        if missing:
            if field == "fuelEconomy":
                # Optional field (e.g. electric vehicles have no fuel economy).
                vehicle[field] = None
            else:
                errors.append(f"'{field}' is required")
            continue
        # Convert to the right type (number or text); report it if that fails.
        cast = TYPE_CASTS.get(field)
        try:
            vehicle[field] = cast(value) if cast else str(value).strip()
        except (TypeError, ValueError):
            errors.append(f"'{field}' must be a {cast.__name__}")

    # If no status is given, a new vehicle starts as AVAILABLE. Otherwise the
    # status must be one of the allowed values (capital letters are ignored).
    status = str(data.get("status", STATUS_AVAILABLE)).strip().upper()
    if status not in VALID_STATUSES:
        errors.append(f"'status' must be one of: {', '.join(sorted(VALID_STATUSES))}")
    vehicle["status"] = status

    if errors:
        abort(400, message="; ".join(errors))
    return vehicle


# --------------------------------------------------------------------------
# Request handlers ("resources")
# Each class below handles one web address. Its methods (get, post, delete)
# match the type of request received:
#   GET    - read information            POST   - create something or perform an action
#   DELETE - remove something
# Common response codes: 200 OK, 201 Created, 400 bad request, 401 not logged in,
# 403 not allowed, 404 not found, 409 conflict (request clashes with current state).
# --------------------------------------------------------------------------
class VehicleCollection(Resource):
    """GET /vehicles - list vehicles, optionally filtered. Open to everyone."""

    def get(self):
        # lenient=True: a bad or missing token just means "treat as anonymous",
        # because anyone may browse vehicles.
        role = get_role(lenient=True)
        results = list(VEHICLES)

        # Apply the text filters. Each filter narrows the list further, so
        # combining several (e.g. branch and colour) gives vehicles matching all of them.
        for name in TEXT_FILTERS:
            value = request.args.get(name)
            if value:
                results = [v for v in results if v[name].lower() == value.strip().lower()]

        # Apply the min/max filters. Vehicles with no value for the field
        # (e.g. no fuel economy) are left out when that filter is used.
        for name, (field, cast) in RANGE_FILTERS.items():
            minimum = parse_number_arg(f"min_{name}", cast)
            maximum = parse_number_arg(f"max_{name}", cast)
            if minimum is not None:
                results = [v for v in results if v[field] is not None and v[field] >= minimum]
            if maximum is not None:
                results = [v for v in results if v[field] is not None and v[field] <= maximum]

        # Each vehicle is passed through present() so that non-admins do not
        # receive the VIN or registration.
        return [present(v, role) for v in results], 200


class VehicleItem(Resource):
    """GET /vehicles/<id> - details of one vehicle. Open to everyone."""

    def get(self, vehicle_id):
        # Admins see extra fields (VIN, registration); everyone else does not.
        role = get_role(lenient=True)
        return present(find_vehicle(vehicle_id), role), 200


class FleetCollection(Resource):
    """POST /fleet - add a new vehicle to the fleet. Admins only."""

    def post(self):
        require_admin()
        vehicle = parse_new_vehicle(request.get_json(silent=True))

        # Everything from here changes shared data, so hold the lock.
        with _lock:
            # VINs and registrations identify a single real-world vehicle, so
            # the same one must not be added twice (capital letters ignored).
            for existing in VEHICLES:
                if existing["vin"].lower() == vehicle["vin"].lower():
                    abort(409, message="A vehicle with this VIN already exists.")
                if existing["vrm"].lower() == vehicle["vrm"].lower():
                    abort(409, message="A vehicle with this VRM already exists.")
            # The new id is one higher than the current highest (or 1 if the
            # fleet is empty). The caller never chooses the id.
            vehicle["id"] = max((v["id"] for v in VEHICLES), default=0) + 1
            # Re-order the fields to match the column order of the file.
            vehicle = {field: vehicle[field] for field in FIELDS}
            VEHICLES.append(vehicle)
            save_vehicles()

        # 201 means "created". The Location header tells the caller where the
        # new vehicle can now be viewed.
        return (
            {"message": "Vehicle added to the fleet.", "vehicleId": vehicle["id"]},
            201,
            {"Location": f"{API_PREFIX}/vehicles/{vehicle['id']}"},
        )


class FleetItem(Resource):
    """DELETE /fleet/<id> - remove a vehicle from the fleet. Admins only."""

    def delete(self, vehicle_id):
        require_admin()
        with _lock:
            vehicle = find_vehicle(vehicle_id)
            # A vehicle that is out on rent cannot be removed; it must be
            # returned first.
            if vehicle["status"] == STATUS_RENTED:
                abort(409, message="Cannot remove a vehicle that is currently rented.")
            VEHICLES.remove(vehicle)
            save_vehicles()
        return {"message": f"Vehicle {vehicle_id} has been removed from the fleet."}, 200


class AuthenticationCollection(Resource):
    """POST /authentications - log in with an email and password.

    On success the caller receives their customer id, whether they are an
    admin, and a token to send with later requests.
    """

    def post(self):
        # Check the request contains an email and a password, both as text.
        data = request.get_json(silent=True)
        if (
            not isinstance(data, dict)
            or not isinstance(data.get("email"), str)
            or not isinstance(data.get("password"), str)
            or not data["email"].strip()
            or not data["password"]
        ):
            abort(400, message="Request body must be JSON containing 'email' and 'password'.")

        # Find the customer by email (capital letters and stray spaces ignored).
        customer = find_customer_by_email(data["email"])
        if customer is not None:
            # The file stores a scrambled version of the password (the "hash")
            # and the customer's salt. To check a login, the password that was
            # just typed is scrambled the same way, and the result is compared
            # with the stored hash. The real password is never stored or compared.
            salt = (customer.get("salt") or "").strip()
            stored_hash = (customer.get("hashedPassword") or "").strip()
            computed_hash = str(hashPassword(data["password"], salt))
            # compare_digest is a comparison built for security: it takes the
            # same amount of time whether the values match closely or not at
            # all, which stops an attacker from learning anything from timing.
            if secrets.compare_digest(computed_hash.encode(), stored_hash.encode()):
                # Use a number for the customer id where possible.
                customer_id = (customer.get("customerId") or "").strip()
                customer_id = int(customer_id) if customer_id.isdigit() else customer_id
                admin = is_admin(customer)
                # Create the login token: a long random string that cannot be
                # guessed. Remember who it belongs to, and whether they are an
                # admin, in the list of logged-in users.
                token = secrets.token_urlsafe(32)
                SESSIONS[token] = {"customerId": customer_id, "admin": admin}
                return {
                    "message": "Authentication successful.",
                    "customerId": customer_id,
                    "admin": admin,
                    "token": token,
                }, 200

        # An unknown email and a wrong password deliberately give the same
        # answer, so nobody can use this service to discover which email
        # addresses have accounts.
        abort(401, message="Invalid email or password.")


class LogoutCollection(Resource):
    """POST /logout - forget the caller's token, ending their session.

    Requires a valid token, sent the same way as any other request:
        Authorization: Bearer <token>
    """

    def post(self):
        header = request.headers.get("Authorization")
        scheme, _, token = header.partition(" ") if header else ("", "", "")
        token = token.strip()
        # Removing the token from SESSIONS is what actually logs the caller
        # out: get_role() will no longer recognise it on later requests.
        if scheme.lower() != "bearer" or token not in SESSIONS:
            abort(401, message="Invalid or expired token. You may already be logged out.")
        del SESSIONS[token]
        return {"message": "Logged out."}, 200


class SignupCollection(Resource):
    """POST /signup - create a new customer account.

    For testing, the plaintext password is stored in the 'password' column as
    well as being hashed. IP address columns are left blank; they are not
    something a customer supplies when signing up.
    """

    def post(self):
        customer = parse_signup_data(request.get_json(silent=True))

        with _lock:
            if find_customer_by_email(customer["email"]) is not None:
                abort(409, message="An account with this email already exists.")

            # The new id is one higher than the current highest (or 1 if there
            # are no customers yet). The caller never chooses the id.
            existing_ids = [
                int(c["customerId"]) for c in CUSTOMERS if (c.get("customerId") or "").strip().isdigit()
            ]
            customer["customerId"] = str(max(existing_ids, default=0) + 1)

            # Scramble the password with a fresh, random salt unique to this
            # customer, so the same password looks different for every account.
            # generateSalt() may return raw bytes or a ready-made hex string,
            # depending on how it's implemented; either way, what gets stored
            # in the CSV and passed to hashPassword must be text (hex digits).
            salt = generateSalt()
            if isinstance(salt, (bytes, bytearray)):
                salt = salt.hex()
            customer["salt"] = salt
            customer["hashedPassword"] = str(hashPassword(customer["password"], salt))
            customer["ip_address_v4"] = ""
            customer["ip_address_v6"] = ""

            CUSTOMERS.append(customer)
            save_customers()

        return {
            "message": "Account created.",
            "customerId": int(customer["customerId"]),
        }, 201


class RentalCollection(Resource):
    """POST /rentals - rent a vehicle (AVAILABLE becomes RENTED). Open to everyone."""

    def post(self):
        vehicle_id = parse_vehicle_id(request.get_json(silent=True))
        # The lock covers checking the status and changing it as one step, so
        # two people cannot both rent the same vehicle at the same instant.
        with _lock:
            vehicle = find_vehicle(vehicle_id)
            # Only an AVAILABLE vehicle can be rented. One that is already
            # rented, or is waiting for a service, is refused.
            if vehicle["status"] != STATUS_AVAILABLE:
                abort(409, message=f"Vehicle {vehicle_id} is not available for rent (current status: {vehicle['status']}).")
            vehicle["status"] = STATUS_RENTED
            save_vehicles()
        return {"message": f"Vehicle {vehicle_id} has been rented."}, 200


class ReturnCollection(Resource):
    """POST /returns - update a vehicle after a rental or a service. Admins only.

    A worker inspects the vehicle and states in the request what happens to it:
        RENTED     -> AVAILABLE   returned and fine to rent again
        RENTED     -> SERVICEREQ  returned, but needs servicing
        SERVICEREQ -> AVAILABLE   servicing finished, fine to rent again
    A vehicle waiting for service (SERVICEREQ) cannot be rented.
    """

    def post(self):
        require_admin()
        body = request.get_json(silent=True)
        vehicle_id = parse_vehicle_id(body)
        # The worker's decision: AVAILABLE or SERVICEREQ.
        new_status = parse_return_status(body)
        with _lock:
            vehicle = find_vehicle(vehicle_id)
            previous_status = vehicle["status"]
            # Only a vehicle that is out on rent, or waiting for a service, can
            # be updated this way.
            if previous_status not in (STATUS_RENTED, STATUS_SERVICE_REQUIRED):
                abort(409, message=f"Vehicle {vehicle_id} is neither rented nor waiting for a service (current status: {previous_status}).")
            # Refuse a request that would change nothing (SERVICEREQ to SERVICEREQ).
            if previous_status == new_status:
                abort(409, message=f"Vehicle {vehicle_id} is already {new_status}.")
            vehicle["status"] = new_status
            save_vehicles()
        # Choose a message that describes what actually happened.
        if new_status == STATUS_SERVICE_REQUIRED:
            message = f"Vehicle {vehicle_id} has been returned and needs servicing before it can be rented again."
        elif previous_status == STATUS_SERVICE_REQUIRED:
            message = f"Vehicle {vehicle_id} has been serviced and is now available."
        else:
            message = f"Vehicle {vehicle_id} has been returned and is now available."
        return {"message": message}, 200


# --------------------------------------------------------------------------
# Web addresses
# Connects each address to the code that handles it. The /api/v1 prefix is added
# automatically by the blueprint, so it is not repeated here. <int:...> in an
# address is a placeholder for a whole number, e.g. /vehicles/3 means vehicle id 3.
# --------------------------------------------------------------------------
api.add_resource(VehicleCollection, "/vehicles")
api.add_resource(VehicleItem, "/vehicles/<int:vehicle_id>")
api.add_resource(FleetCollection, "/fleet")
api.add_resource(FleetItem, "/fleet/<int:vehicle_id>")
api.add_resource(AuthenticationCollection, "/authentications")
api.add_resource(LogoutCollection, "/logout")
api.add_resource(SignupCollection, "/signup")
api.add_resource(RentalCollection, "/rentals")
api.add_resource(ReturnCollection, "/returns")