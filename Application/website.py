import requests
import hashlib
from flask import flash, Blueprint, render_template, request, redirect, url_for, abort
from user import user
 
website_bp = Blueprint("website", __name__, template_folder="templates")

API_BASE = "http://127.0.0.1:5000/api/v1"


@website_bp.context_processor   
def inject_user():
    return {"user": user}

 
@website_bp.route("/")
def index():
    headers = {}
    params = {"status": "AVAILABLE"}
    if user.is_logged_in:
        headers["Authorization"] = f"Bearer {user.session_token}"
        if user.licence_restrictions:
            params["category"] = user.licence_restrictions

    response = requests.get(f"{API_BASE}/vehicles", params=params, headers=headers)
    vehicles = response.json()[:6]
    return render_template("index.html", first_name=user.first_name, last_name=user.last_name, vehicles=vehicles)


@website_bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'GET':
        return render_template("login.html")
    form_data = {
        "email": request.form.get("email"),
        "password": request.form.get("password")
    }

    response = requests.post(
        f"{API_BASE}/authentications",
        json={"email": form_data["email"], "password": form_data["password"]},
    )

    if response.status_code != 200:
        flash(response.json()["message"], "error")
        return render_template("login.html")

    data = response.json()
    user.session_token = data["token"]
    user.customer_id = data["customerId"]
    user.admin = data["admin"]

    headers = {}
    headers["Authorization"] = f"Bearer {user.session_token}"
    response = requests.get(f"{API_BASE}/me", headers=headers)

    me = response.json()

    user.first_name = me["first_name"]
    user.last_name = me["last_name"]
    user.dob = me["dob"]
    user.gender = me["gender"]
    user.email = me["email"]
    user.address = me["address"]
    user.city = me["city"]
    user.country = me["country"]
    user.driving_licence_number = me["drivingLicenseNumber"]
    user.passport_number = me["passportNumber"]
    user.licence_restrictions = me["LicenseRetrictions"]

    return redirect(url_for("website.index"))


@website_bp.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == "GET":
        return render_template("signup.html")

    response = requests.post(f"{API_BASE}/signup", json=request.form.to_dict())
 
    # 400 = a field is missing or blank, 409 = email already registered.
    # Either way the API tells us what went wrong, so just show that.
    if response.status_code != 201:
        flash(response.json()["message"], "error")
        return render_template("signup.html")
 
    flash("Account created. You can log in now.")
    return redirect(url_for("website.login"))
 

@website_bp.route('/vehicles')
def vehicles():
    return render_template('vehicles.html')

@website_bp.route("/logout")
def logout():
    if user.session_token:
        response = requests.post(
            f"{API_BASE}/logout",
            headers={"Authorization": f"Bearer {user.session_token}"},
        )
        if response.status_code != 200:
            print("API logout:", response.status_code, response.text)

    user.log_out()
    return redirect(url_for("website.index"))

@website_bp.route('/vehicles/<int:vehicle_id>')
def vehicle(vehicle_id):
    headers = {}
    if user.session_token:
        headers["Authorization"] = f"Bearer {user.session_token}"

    response = requests.get(f"{API_BASE}/vehicles/{vehicle_id}", headers=headers)

    if response.status_code != 200:
        abort(404)

    return render_template("vehicle.html", vehicle=response.json())

@website_bp.route("/vehicles/<int:vehicle_id>/return", methods=["POST"])
def return_vehicle(vehicle_id):
    response = requests.post(
        f"{API_BASE}/returns",
        # vehicle_id is a real int thanks to <int:...> — the API rejects "7".
        # status comes from whichever button was clicked.
        json={"vehicleId": vehicle_id, "status": request.form["status"]},
        headers={"Authorization": f"Bearer {user.session_token}"},
    )
 
    if response.status_code == 200:
        flash(response.json()["message"])
    else:
        print("return failed:", response.status_code, response.text)
        flash(response.json()["message"], "error")
 
    return redirect(url_for("website.vehicle", vehicle_id=vehicle_id))

@website_bp.route("/vehicles/<int:vehicle_id>/rent", methods=["POST"])
def rent_vehicle(vehicle_id):
    headers = {}
    if user.session_token:
        headers["Authorization"] = f"Bearer {user.session_token}"

    response = requests.post(f"{API_BASE}/rentals", json={"vehicleId": vehicle_id}, headers=headers)

    if response.status_code == 200:
        flash(response.json()["message"])
    else:
        print("rent failed:", response.status_code, response.text)
        flash(response.json()["message"], "error")

    return redirect(url_for("website.vehicle", vehicle_id=vehicle_id))

@website_bp.route("/vehicles/<int:vehicle_id>/status", methods=["POST"])
def change_vehicle_status(vehicle_id):
    headers = {}
    if user.session_token and user.is_admin:
        headers["Authorization"] = f"Bearer {user.session_token}"
    else:
        flash("Please log in with an admin account to perform this action", "error")
        return redirect(url_for("website.vehicle", vehicle_id=vehicle_id))

    status = request.form.get("status")

    response = requests.patch(
        f"{API_BASE}/fleet/{vehicle_id}",
        json={"status": status},
        headers=headers,
    )

    if response.status_code == 200:
        flash(response.json()["message"])
    else:
        flash(response.json()["message"], "error")

    return redirect(url_for("website.vehicle", vehicle_id=vehicle_id))

@website_bp.route("/vehicles/<int:vehicle_id>/rate", methods=["POST"])
def change_vehicle_dayrate(vehicle_id):
    headers = {}
    if user.session_token and user.is_admin:
        headers["Authorization"] = f"Bearer {user.session_token}"
    else:
        flash("Please log in with an admin account to perform this action", "error")
        return redirect(url_for("website.vehicle", vehicle_id=vehicle_id))

    dayRate = int(request.form.get("dayRate"))

    response = requests.patch(
        f"{API_BASE}/fleet/{vehicle_id}",
        json={"dayRate": dayRate},
        headers=headers,
    )

    if response.status_code == 200:
        flash(response.json()["message"])
    else:
        flash(response.json()["message"], "error")

    return redirect(url_for("website.vehicle", vehicle_id=vehicle_id))

@website_bp.route("/vehicles/<int:vehicle_id>/branch", methods=["POST"])
def change_vehicle_branch(vehicle_id):
    headers = {}
    if user.session_token and user.is_admin:
        headers["Authorization"] = f"Bearer {user.session_token}"
    else:
        flash("Please log in with an admin account to perform this action", "error")
        return redirect(url_for("website.vehicle", vehicle_id=vehicle_id))

    branch = request.form.get("branch")

    response = requests.patch(
        f"{API_BASE}/fleet/{vehicle_id}",
        json={"branch": branch},
        headers=headers,
    )

    if response.status_code == 200:
        flash(response.json()["message"])
    else:
        flash(response.json()["message"], "error")

    return redirect(url_for("website.vehicle", vehicle_id=vehicle_id))

@website_bp.route("/vehicles/<int:vehicle_id>/delete", methods=["POST"])
def delete_vehicle(vehicle_id):
    headers = {}
    if user.session_token and user.is_admin:
        headers["Authorization"] = f"Bearer {user.session_token}"
    else:
        flash("Please log in with an admin account to perform this action", "error")
        return redirect(url_for("website.vehicle", vehicle_id=vehicle_id))

    response = requests.delete(f"{API_BASE}/fleet/{vehicle_id}", headers=headers)

    if response.status_code == 200:
        flash(response.json()["message"])
    else:
        flash(response.json()["message"], "error")

    return redirect(url_for("website.vehicles"))

@website_bp.route("/newcar", methods=["GET", "POST"])
def newcar():
    if request.method == "GET":
        return render_template("newcar.html")

    headers = {}
    if user.session_token and user.is_admin:
        headers["Authorization"] = f"Bearer {user.session_token}"
    else:
        flash("Please log in with an admin account to perform this action", "error")
        return render_template("newcar.html")
    
    response = requests.post(f"{API_BASE}/fleet", json=request.form.to_dict(), headers=headers)

    if response.status_code != 201:
        flash(response.json()["message"], "error")
        return render_template("newcar.html")
 
    flash("Vehicle added to the fleet")
    return redirect(url_for("website.vehicle", vehicle_id=response.json()["vehicleId"]))

@website_bp.route("/regsearch")
def regsearch():
    q = request.args.get("q")

    # No q means the page was just opened, not submitted.
    if not q:
        return render_template("regsearch.html")

    headers = {}
    if user.session_token and user.is_admin:
        headers["Authorization"] = f"Bearer {user.session_token}"
    else:
        flash("Please log in with an admin account to perform this action", "error")
        return render_template("regsearch.html")

    response = requests.get(
        f"{API_BASE}/vehicles",
        params={"vrm": q.replace(" ", "")},
        headers=headers,
    )

    if response.status_code != 200:
        flash(response.json()["message"], "error")
        return render_template("regsearch.html")

    results = response.json()
    if not results:
        flash(f"No vehicle found with registration {q}.", "error")
        return render_template("regsearch.html")

    return redirect(url_for("website.vehicle", vehicle_id=results[0]["id"]))

    