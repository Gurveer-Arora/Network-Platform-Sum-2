import requests
import hashlib
from flask import Blueprint, render_template, request, redirect, url_for
 
website_bp = Blueprint("website", __name__, template_folder="templates")
 
 
@website_bp.route("/")
def index():
    return render_template("index.html")


@website_bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'GET':
        return render_template("login.html")
    form_data = {
        "email": request.form.get("email"),
        "password": request.form.get("password")
    }

    validateLogin(form_data["email"], form_data["password"])

    return render_template(
        'success.html',
        email=form_data["email"],
        password=form_data["password"]
    )

def validateLogin(email, password):
    return
    ### API Request to get the Salt for user with email provided
    API_GET_EMAIL = "api call" # CALLS API WITH EMAIL PROVIDED, RETURNS CODE, PASSWORD HASH, SALT, USER ID
    if API_GET_EMAIL["return code"] == 404:
        return API_GET_EMAIL, "Email not found" #RETURNS API RESULT AND ERROR MESSAGE
    password_bytes = password.encode("utf-8")
    salt = bytes.fromhex(API_GET_EMAIL["salt"])
    iterations = 600000
    hashed_password = hashlib.pbkdf2_hmac("sha256", password_bytes, salt, iterations).hex()

    if hashed_password == API_GET_EMAIL["hashed password"]:
        return API_GET_EMAIL["uuid"]

@website_bp.route('/signup', methods=['GET', 'POST'])
def signup():
    return render_template('signup.html')