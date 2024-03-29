import csv
import json
import typing
from http import HTTPStatus
from io import StringIO
from sqlite3 import OperationalError

import flask
import flask_restful

from scoutingbackend import schemes
from scoutingbackend.database import db, generate_selector
from scoutingbackend.restfulerror import RestfulErrorApi


class Api(object):
    def __init__(self) -> None:
        self.bp = flask.Blueprint('api', __name__, url_prefix='/api')

        self.rest = RestfulErrorApi(self.bp)
        self.rest.add_resource(self.Tables, '/<int:season>/tables')
        self.rest.add_resource(self.ApiMSchema, '/<int:season>/matchschema')
        self.rest.add_resource(self.ApiPSchema, '/<int:season>/pitschema')
        self.rest.add_resource(self.ApiPit, '/<int:season>/<string:event>/pit')
        self.rest.add_resource(self.ApiPitCsv, '/<int:season>/<string:event>/pit/csv')
        self.rest.add_resource(self.ApiMatch, '/<int:season>/<string:event>/match')
        self.rest.add_resource(self.ApiCompletion, '/<int:season>/<string:event>/completion')

        self.pit = self.ApiPit

    def register(self, app: typing.Union[flask.Flask, flask.Blueprint]):
        app.register_blueprint(self.bp)

    class Tables(flask_restful.Resource):
        def get(self, season: int):
            tables = db.connection().execute("SELECT name from sqlite_master WHERE type='table'").fetchall()
            return [event['name'] for event in tables if season <= 0 or event['name'].startswith(f'frc{season}')]
        
        def put(self, season: int):
            if str(season) not in schemes.MATCH_SCHEME or str(season) not in schemes.PIT_SCHEME:
                return flask.Response("No Season Schema", HTTPStatus.NOT_FOUND)
            data = flask.request.get_data()
            if not data:
                return flask.Response("No Data", HTTPStatus.BAD_REQUEST)
            event_name = data.decode(flask.request.charset)

            db.create_tables(season, event_name)
            return flask.Response(status=HTTPStatus.OK)

    class ApiMSchema(flask_restful.Resource):
        def get(self, season: int):
            if str(season) not in schemes.MATCH_SCHEME:
                return flask.Response("No Season Schema", HTTPStatus.NOT_FOUND)
            return flask.Response(json.dumps(schemes.MATCH_SCHEME[str(season)], sort_keys=False), 200, content_type='application/json')

    class ApiPSchema(flask_restful.Resource):
        def get(self, season: int):
            if str(season) not in schemes.PIT_SCHEME:
                return flask.Response("No Season Schema", HTTPStatus.NOT_FOUND)
            return flask.Response(json.dumps(schemes.PIT_SCHEME[str(season)], sort_keys=False), 200, content_type='application/json')

    class ApiPit(flask_restful.Resource):
        def post(self, season: int, event: str):
            c = db.connection()
            if c.execute(f"SELECT * FROM sqlite_master WHERE type='table' AND name='frc{season}{event}_pit'").fetchone() is None:
                return flask.Response("Event Table does not exist", HTTPStatus.NOT_FOUND, mimetype="text/plain")
            input_data = flask.request.get_json(force=True)
            if not input_data:
                return flask.Response(status=HTTPStatus.BAD_REQUEST, mimetype="text/plain")
            if ("teamNumber" not in input_data or input_data["teamNumber"] is None) or ("name" not in input_data or input_data["name"] is None):
                return flask.Response("Missing teamNumber / name", HTTPStatus.BAD_REQUEST, mimetype="text/plain")
            if c.execute(f"SELECT name FROM frc{season}{event}_pit WHERE name='{input_data['name']}' AND teamNumber='{input_data['teamNumber']}'").fetchone() is not None:
                return flask.Response("Duplicate Submission", HTTPStatus.METHOD_NOT_ALLOWED, mimetype="text/plain", headers={"Allow": "PATCH"})
            
            c.cursor().execute(f"INSERT INTO frc{season}{event}_pit ({', '.join(input_data.keys())}) VALUES ({('?, '*len(input_data)).rstrip(', ')})", tuple(input_data.values()))
            c.commit()
            return flask.Response(status=HTTPStatus.OK)

        def get(self, season, event):
            c = db.connection().cursor()
            if c.execute(f"SELECT * FROM sqlite_master WHERE type='table' AND name='frc{season}{event}_pit'").fetchone() is None:
                return flask.Response("Event Table does not exist", HTTPStatus.NOT_FOUND)
            try:
                values = c.execute(f"SELECT * FROM frc{season}{event}_pit {generate_selector(flask.request.args)}").fetchall()
            except OperationalError:
                return flask.Response("Invalid Selectors", HTTPStatus.BAD_REQUEST)
            if len(values) == 0:
                return flask.Response("No Values Found", HTTPStatus.NOT_FOUND)
            return [dict(scout) for scout in values]
        
        def patch(self, season: int, event: str):
            c = db.connection()
            if c.execute(f"SELECT * FROM sqlite_master WHERE type='table' AND name='frc{season}{event}_pit'").fetchone() is None:
                return flask.Response("Event Table does not exist", HTTPStatus.NOT_FOUND)
            input_data = flask.request.get_json(force=True)
            if not input_data:
                return flask.Response(status=HTTPStatus.BAD_REQUEST, mimetype="text/plain")
            if ("teamNumber" not in input_data or input_data["teamNumber"] is None) or ("name" not in input_data or input_data["name"] is None):
                return flask.Response("Missing teamNumber / name", HTTPStatus.BAD_REQUEST, mimetype="text/plain")
            
            if c.execute(f"SELECT * FROM frc{season}{event}_pit WHERE teamNumber={input_data['teamNumber']} AND name='{input_data['name']}'").fetchone() is None:
                return flask.Response("Nothing to Edit", HTTPStatus.METHOD_NOT_ALLOWED, mimetype="text/plain", headers={"Allow": "POST"})
            
            body = ', '.join([f"{k}='{v}'" for k, v in input_data.items() if k != "teamNumber" and k != "name"])
            c.cursor().execute(f"UPDATE frc{season}{event}_pit SET {body} WHERE teamNumber={input_data['teamNumber']} AND name='{input_data['name']}'")
            c.commit()
            return flask.Response(status=HTTPStatus.OK)
    
    class ApiPitCsv(flask_restful.Resource):
        def get(self, season: int, event: str):
            c = db.connection().cursor()
            if c.execute(f"SELECT * FROM sqlite_master WHERE type='table' AND name='frc{season}{event}_pit'").fetchone() is None:
                return flask.Response("Event Table does not exist", HTTPStatus.NOT_FOUND)
            try:
                values = c.execute(f"SELECT * FROM frc{season}{event}_pit {generate_selector(flask.request.args)}").fetchall()
            except OperationalError:
                return flask.Response("Invalid Selectors", HTTPStatus.BAD_REQUEST)
            if len(values) == 0:
                return flask.Response("No Values Found", HTTPStatus.NOT_FOUND)
            out = StringIO()
            writer = csv.DictWriter(out, fieldnames=["name", "teamNumber", *schemes.PIT_SCHEME[str(season)].keys()])
            writer.writeheader()
            writer.writerows([{k: v for (k, v) in dict(scout).items() if k in writer.fieldnames} for scout in values])
            return flask.Response(out.getvalue(), HTTPStatus.OK, mimetype='text/csv')

    class ApiMatch(flask_restful.Resource):
        def post(self, season: int, event: str):
            c = db.connection()
            if c.execute(f"SELECT * FROM sqlite_master WHERE type='table' AND name='frc{season}{event}_match'").fetchone() is None:
                return flask.Response("Event Table does not exist", HTTPStatus.NOT_FOUND, mimetype="text/plain")
            input_data = flask.request.get_json(force=True)
            if not input_data:
                return flask.Response(status=HTTPStatus.BAD_REQUEST, mimetype="text/plain")
            if ("teamNumber" not in input_data or input_data["teamNumber"] is None) or ("match" not in input_data or input_data["match"] is None) or ("name" not in input_data or input_data["name"] is None):
                return flask.Response("Missing teamNumber / match / name", HTTPStatus.BAD_REQUEST, mimetype="text/plain")
            if c.execute(f"SELECT name FROM frc{season}{event}_match WHERE match='{input_data['match']}' AND teamNumber='{input_data['teamNumber']}'").fetchone() is not None:
                return flask.Response("Duplicate Submission", HTTPStatus.CONFLICT, mimetype="text/plain")
            if not input_data['teamNumber'] in flask.current_app.bluealliance.match.get(season, event, input_data['match']):
                return flask.Response("Invalid teamNumber / match", HTTPStatus.BAD_REQUEST, mimetype="text/plain")

            submit_data = {}
            for key, value in input_data.items():
                if isinstance(value, dict):
                    for key1, value1 in value.items():
                        submit_data[key+key1[0].upper()+key1[1:]] = value1
                else:
                    submit_data[key] = value

            c.cursor().execute(f"INSERT INTO frc{season}{event}_match ({', '.join(submit_data.keys())}) VALUES ({('?, '*len(submit_data)).rstrip(', ')})", tuple(submit_data.values()))
            c.commit()
            return flask.Response(status=HTTPStatus.OK)
        
        def get(self, season: int, event: str):
            c = db.connection().cursor()
            if c.execute(f"SELECT * FROM sqlite_master WHERE type='table' AND name='frc{season}{event}_match'").fetchone() is None:
                return flask.Response("Event Table does not exist", HTTPStatus.NOT_FOUND)
            try:
                values = c.execute(f"SELECT * FROM frc{season}{event}_match {generate_selector(flask.request.args)}").fetchall()
            except OperationalError:
                return flask.Response("Invalid Selectors", HTTPStatus.BAD_REQUEST)
            if len(values) == 0:
                return flask.Response("No Values Found", HTTPStatus.NOT_FOUND)
            return [dict(scout) for scout in values]
    
    class ApiCompletion(flask_restful.Resource):
        def get(self, season: int, event: str):
            c = db.connection().cursor()
            if c.execute(f"SELECT * FROM sqlite_master WHERE type='table' AND name='frc{season}{event}_match'").fetchone() is None:
                return flask.Response("Event Table does not exist", HTTPStatus.NOT_FOUND)
            allmatches = flask.request.args.get("all") == "true"
            flask.g.args = {"ignoreDate": "false"}
            matches: typing.Dict[str, str] = flask.current_app.bluealliance.event.get(season, event)
            total = len([matchk for matchk in matches.keys() if allmatches or matchk.startswith("qm")])*6
            try:
                values = c.execute(f"SELECT * FROM frc{season}{event}_match").fetchall()
                filled = len(set([f"{r['match']}_{r['teamNumber']}" for r in values if allmatches or r['match'].startswith("qm")]))
            except OperationalError:
                return flask.Response(status=HTTPStatus.BAD_REQUEST)
            return {"total": total, "filled": filled, "percent": "{0:.0%}".format(filled/total) if total > 0 else -1}