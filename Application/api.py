import csv
import os
import tempfile
import threading

from flask import Blueprint, request
from flask_restful import Api, Resource, abort

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------
API_PREFIX = "/api/v1"
VEHICLE_CSV = "vehicle.csv"

# Temporary, until real authentication exists: the role every request is treated as.
# "admin" = full access; set to "user" to see the regular-user view (admin endpoints will return 403).
DEFAULT_ROLE = "admin"

STATUS_AVAILABLE = "AVAILABLE"
STATUS_RENTED = "RENTED"
VALID_STATUSES = {STATUS_AVAILABLE, STATUS_RENTED}

FIELDS = [
    "id", "make", "model", "colour", "vin", "year", "vrm",
    "category", "numberSeats", "dayRate", "status", "fuelEconomy", "branch",
]
ADMIN_ONLY_FIELDS = {"vin", "vrm"}  # hidden from regular/anonymous users
TYPE_CASTS = {
    "id": int, "year": int, "numberSeats": int,
    "dayRate": float, "fuelEconomy": float,
}

# query-string filters: exact match, case-insensitive
TEXT_FILTERS = ["make", "model", "colour", "category", "branch", "status"]
# query-string filters: min_<name> / max_<name> -> (csv field, cast)
RANGE_FILTERS = {
    "year": ("year", int),
    "seats": ("numberSeats", int),
    "day_rate": ("dayRate", float),
    "fuel_economy": ("fuelEconomy", float),
}

api_bp = Blueprint("api", __name__, url_prefix=API_PREFIX)
api = Api(api_bp)

# --------------------------------------------------------------------------
# Data access (CSV kept in memory, written back on every change)
# --------------------------------------------------------------------------
_lock = threading.Lock()


def _parse_row(row):
    vehicle = {}
    for field in FIELDS:
        raw = (row.get(field) or "").strip()
        cast = TYPE_CASTS.get(field)
        if cast is None:
            vehicle[field] = raw
        else:
            vehicle[field] = cast(raw) if raw != "" else None
    vehicle["status"] = vehicle["status"].upper()  # tolerate 'Available' etc. in the file
    return vehicle


def _detect_delimiter(path):
    """Comma or tab, whichever the header row uses."""
    with open(path, newline="", encoding="utf-8-sig") as f:
        header = f.readline()
    return "\t" if header.count("\t") > header.count(",") else ","


CSV_DELIMITER = _detect_delimiter(VEHICLE_CSV)


def load_vehicles():
    # utf-8-sig strips the hidden BOM that Excel adds to "CSV UTF-8" files
    with open(VEHICLE_CSV, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f, delimiter=CSV_DELIMITER)
        reader.fieldnames = [name.strip() for name in reader.fieldnames or []]
        missing = [field for field in FIELDS if field not in reader.fieldnames]
        if missing:
            raise RuntimeError(f"{VEHICLE_CSV} is missing column(s): {', '.join(missing)}")
        return [_parse_row(row) for row in reader]


def save_vehicles():
    """Atomically write VEHICLES back to the CSV. Call while holding _lock."""
    directory = os.path.dirname(os.path.abspath(VEHICLE_CSV))
    fd, tmp_path = tempfile.mkstemp(dir=directory, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDS, delimiter=CSV_DELIMITER)
            writer.writeheader()
            for vehicle in VEHICLES:
                writer.writerow({k: ("" if v is None else v) for k, v in vehicle.items()})
        os.replace(tmp_path, VEHICLE_CSV)
    except Exception:
        os.unlink(tmp_path)
        raise


VEHICLES = load_vehicles()

# --------------------------------------------------------------------------
# Authentication / authorisation
# --------------------------------------------------------------------------


def get_role():
    """Return the caller's role: 'admin', 'user' or None (anonymous).

    TODO: replace with the real check against the admin accounts CSV.
    """
    return DEFAULT_ROLE


def require_admin():
    """Abort unless the caller is an admin."""
    if get_role() != "admin":
        abort(403, message="Admin access required.")


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def present(vehicle, role):
    """Admins see every field; everyone else has admin-only fields removed."""
    if role == "admin":
        return dict(vehicle)
    return {k: v for k, v in vehicle.items() if k not in ADMIN_ONLY_FIELDS}


def find_vehicle(vehicle_id):
    for vehicle in VEHICLES:
        if vehicle["id"] == vehicle_id:
            return vehicle
    abort(404, message=f"Vehicle {vehicle_id} not found.")


def parse_number_arg(name, cast):
    raw = request.args.get(name)
    if raw is None or raw == "":
        return None
    try:
        return cast(raw)
    except ValueError:
        abort(400, message=f"Query parameter '{name}' must be a {cast.__name__}.")


def parse_vehicle_id(data):
    """Read and validate vehicleId from a JSON request body."""
    if not isinstance(data, dict) or "vehicleId" not in data:
        abort(400, message="Request body must be JSON containing 'vehicleId'.")
    vehicle_id = data["vehicleId"]
    if isinstance(vehicle_id, bool) or not isinstance(vehicle_id, int):
        abort(400, message="'vehicleId' must be an integer.")
    return vehicle_id


def parse_new_vehicle(data):
    """Validate the JSON body for POST /vehicles and return a clean vehicle dict."""
    if not isinstance(data, dict):
        abort(400, message="Request body must be a JSON object.")

    errors, vehicle = [], {}
    for field in FIELDS:
        if field in ("id", "status"):  # id is generated, status defaults to available
            continue
        value = data.get(field)
        missing = value is None or (isinstance(value, str) and not value.strip())
        if missing:
            if field == "fuelEconomy":  # optional (e.g. electric vehicles)
                vehicle[field] = None
            else:
                errors.append(f"'{field}' is required")
            continue
        cast = TYPE_CASTS.get(field)
        try:
            vehicle[field] = cast(value) if cast else str(value).strip()
        except (TypeError, ValueError):
            errors.append(f"'{field}' must be a {cast.__name__}")

    status = str(data.get("status", STATUS_AVAILABLE)).strip().upper()
    if status not in VALID_STATUSES:
        errors.append(f"'status' must be one of: {', '.join(sorted(VALID_STATUSES))}")
    vehicle["status"] = status

    if errors:
        abort(400, message="; ".join(errors))
    return vehicle


# --------------------------------------------------------------------------
# Resources
# --------------------------------------------------------------------------
class VehicleCollection(Resource):
    def get(self):
        """List vehicles. Filters: make, model, colour, category, branch, status,
        min_/max_ year, seats, day_rate, fuel_economy."""
        role = get_role()
        results = list(VEHICLES)

        for name in TEXT_FILTERS:
            value = request.args.get(name)
            if value:
                results = [v for v in results if v[name].lower() == value.strip().lower()]

        for name, (field, cast) in RANGE_FILTERS.items():
            minimum = parse_number_arg(f"min_{name}", cast)
            maximum = parse_number_arg(f"max_{name}", cast)
            if minimum is not None:
                results = [v for v in results if v[field] is not None and v[field] >= minimum]
            if maximum is not None:
                results = [v for v in results if v[field] is not None and v[field] <= maximum]

        return [present(v, role) for v in results], 200


class VehicleItem(Resource):
    def get(self, vehicle_id):
        """Vehicle details (admins see extra fields)."""
        role = get_role()
        return present(find_vehicle(vehicle_id), role), 200


class FleetCollection(Resource):
    def post(self):
        """Add a vehicle to the fleet (admin only)."""
        require_admin()
        vehicle = parse_new_vehicle(request.get_json(silent=True))

        with _lock:
            for existing in VEHICLES:
                if existing["vin"].lower() == vehicle["vin"].lower():
                    abort(409, message="A vehicle with this VIN already exists.")
                if existing["vrm"].lower() == vehicle["vrm"].lower():
                    abort(409, message="A vehicle with this VRM already exists.")
            vehicle["id"] = max((v["id"] for v in VEHICLES), default=0) + 1
            vehicle = {field: vehicle[field] for field in FIELDS}  # keep column order
            VEHICLES.append(vehicle)
            save_vehicles()

        return (
            {"message": "Vehicle added to the fleet.", "vehicleId": vehicle["id"]},
            201,
            {"Location": f"{API_PREFIX}/vehicles/{vehicle['id']}"},
        )


class FleetItem(Resource):
    def delete(self, vehicle_id):
        """Remove a vehicle from the fleet (admin only)."""
        require_admin()
        with _lock:
            vehicle = find_vehicle(vehicle_id)
            if vehicle["status"] == STATUS_RENTED:
                abort(409, message="Cannot remove a vehicle that is currently rented.")
            VEHICLES.remove(vehicle)
            save_vehicles()
        return {"message": f"Vehicle {vehicle_id} has been removed from the fleet."}, 200


class RentalCollection(Resource):
    def post(self):
        """Rent a vehicle (available -> rented). Body: {"vehicleId": <int>}."""
        vehicle_id = parse_vehicle_id(request.get_json(silent=True))
        with _lock:
            vehicle = find_vehicle(vehicle_id)
            if vehicle["status"] != STATUS_AVAILABLE:
                abort(409, message=f"Vehicle {vehicle_id} is not available for rent (current status: {vehicle['status']}).")
            vehicle["status"] = STATUS_RENTED
            save_vehicles()
        return {"message": f"Vehicle {vehicle_id} has been rented."}, 200


class ReturnCollection(Resource):
    def post(self):
        """Return a vehicle (rented -> available). Admin only. Body: {"vehicleId": <int>}."""
        require_admin()
        vehicle_id = parse_vehicle_id(request.get_json(silent=True))
        with _lock:
            vehicle = find_vehicle(vehicle_id)
            if vehicle["status"] != STATUS_RENTED:
                abort(409, message=f"Vehicle {vehicle_id} is not currently rented (current status: {vehicle['status']}).")
            vehicle["status"] = STATUS_AVAILABLE
            save_vehicles()
        return {"message": f"Vehicle {vehicle_id} has been returned and is now available."}, 200


api.add_resource(VehicleCollection, "/vehicles")
api.add_resource(VehicleItem, "/vehicles/<int:vehicle_id>")
api.add_resource(FleetCollection, "/fleet")
api.add_resource(FleetItem, "/fleet/<int:vehicle_id>")
api.add_resource(RentalCollection, "/rentals")
api.add_resource(ReturnCollection, "/returns")