from flask import Blueprint
from flask_restful import Api, Resource, abort

api_bp = Blueprint("api", __name__, url_prefix="/api")
api = Api(api_bp)


class NotAllowed(Resource):
    def get(self):
        abort(403)


class HelloWorld(Resource):
    def get(self):
        return {"hello": "world"}


api.add_resource(NotAllowed, "/")
api.add_resource(HelloWorld, "/login/verification")